# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""EMI rule EM-011 (plane-cavity resonance vs clock harmonics)."""

from __future__ import annotations

import math
from collections.abc import Iterable

import shapely
from pydantic import Field

from oa_erc.base import C0, ErcContext, ErcRule, RuleConfig, material_dk
from oa_erc.remediation import TypedAction, TypedRemediation
from oa_model.stackup import DielectricLayer
from oa_results.violations import Violation


def cavity_modes(
    a: float, b: float, eps_r: float, f_max: float, max_index: int = 20
) -> list[tuple[float, int, int]]:
    """Resonances ``f_mn = c / (2 sqrt(eps_r)) sqrt((m/a)^2 + (n/b)^2)`` up to ``f_max`` [Hz]
    of an a x b rectangular plane pair, sorted by frequency (research 07 E11)."""
    out = []
    v = C0 / (2 * math.sqrt(eps_r))
    for m in range(max_index + 1):
        for n in range(max_index + 1):
            if m == n == 0:
                continue
            f = v * math.hypot(m / a, n / b)
            if f <= f_max:
                out.append((f, m, n))
    return sorted(out)


class EM011Config(RuleConfig):
    clock_frequencies: tuple[float, ...] = Field(
        default=(),
        description="Clock/source fundamentals [Hz] to check (none given = rule reports nothing).",
    )
    harmonics: int = Field(default=20, ge=1, description="Harmonics of each clock to compare.")
    tolerance: float = Field(
        default=0.05,
        gt=0,
        description="A mode within this relative distance [fraction] of a harmonic is reported "
        "(E11: +-5 %).",
    )
    min_plane_area: float = Field(
        default=1e-4, gt=0, description="Smallest overlap area [m^2] of a plane pair (100 mm^2)."
    )


class PlaneCavity(ErcRule):
    id = "EM-011"
    title = "Plane-cavity resonance vs clock harmonics"
    category = "emi"
    aliases = ("E11",)
    default_severity = "warning"
    references = (
        "docs/research/07-quasistatic-thermal-emi.md section 3.1 (E11: f_mn = c/(2 sqrt(er)) "
        "sqrt((m/a)^2 + (n/b)^2), +-5 %)",
    )
    Config = EM011Config
    explanation = (
        "Two adjacent planes of different nets form a rectangular cavity. A clock harmonic near "
        "a cavity mode excites it and the board radiates from the plane edges (E11). The cavity "
        "is the minimum rotated rectangle of the plane overlap; modes use the Dk of the "
        "dielectric between the planes. Simplification: the 'fed by a high-speed via' condition "
        "of E11 is not checked."
    )

    def check(self, ctx: ErcContext, cfg: EM011Config) -> Iterable[Violation]:
        if not cfg.clock_frequencies:
            return
        ctx.geometry()
        board = ctx.board
        order = list(board.stackup.layers)
        mats = {m.name: m for m in board.stackup.materials}
        fmax = max(cfg.clock_frequencies) * cfg.harmonics * (1 + cfg.tolerance)
        for i, lyr in enumerate(order[:-2]):
            if lyr.kind != "conductor":
                continue
            diel, lower = order[i + 1], order[i + 2]
            if not isinstance(diel, DielectricLayer) or lower.kind != "conductor":
                continue
            top = _plane_copper(ctx, lyr.id)
            bot = _plane_copper(ctx, lower.id)
            for na, pa in sorted(top.items()):
                for nb, pb in sorted(bot.items()):
                    if na == nb:
                        continue
                    ov = pa.intersection(pb)
                    if ov.area < cfg.min_plane_area:
                        continue
                    rect = shapely.minimum_rotated_rectangle(ov)
                    xs = list(rect.exterior.coords)  # type: ignore[attr-defined]
                    s1 = math.dist(xs[0], xs[1])
                    s2 = math.dist(xs[1], xs[2])
                    a, b = max(s1, s2), min(s1, s2)
                    eps = material_dk(mats[diel.material], cfg.clock_frequencies[0])
                    c = ov.centroid
                    for f, m, n in cavity_modes(a, b, eps, fmax):
                        hit = _nearest_harmonic(f, cfg)
                        if hit is None:
                            continue
                        h, clk, k = hit
                        yield self.violation(
                            message=f"Cavity of planes {na}/{nb} ({lyr.id}-{lower.id}, "
                            f"{a * 1e3:.0f} x {b * 1e3:.0f} mm) resonates at {f / 1e9:.3f} GHz "
                            f"(mode {m},{n}), near harmonic {k} of {clk / 1e6:g} MHz.",
                            location=(c.x, c.y),
                            nets=(na, nb),
                            layers=(lyr.id, lower.id),
                            measured=f,
                            limit=h,
                            unit="Hz",
                            remediation=TypedRemediation(
                                summary=f"Move the {m},{n} cavity mode of {na}/{nb} away from "
                                f"{h / 1e9:.3f} GHz by stitching the planes or adding decoupling.",
                                actions=(
                                    TypedAction(
                                        kind="add_stitching_vias",
                                        description=f"Add stitching vias or capacitors between "
                                        f"{na} and {nb} to damp the cavity.",
                                        targets=(na, nb),
                                        applies_via="board_edit",
                                        confidence="low",
                                    ),
                                    TypedAction(
                                        kind="change_stackup",
                                        description="Thin the dielectric between the planes to "
                                        "increase damping.",
                                        targets=(lyr.id, lower.id),
                                        applies_via="stackup",
                                        confidence="low",
                                    ),
                                ),
                                evidence={
                                    "mode": [m, n],
                                    "a_m": a,
                                    "b_m": b,
                                    "eps_r": eps,
                                    "clock_hz": clk,
                                    "harmonic": k,
                                },
                            ),
                        )


def _nearest_harmonic(f: float, cfg: EM011Config) -> tuple[float, float, int] | None:
    best: tuple[float, float, int] | None = None
    for clk in cfg.clock_frequencies:
        k = max(1, round(f / clk))
        if k > cfg.harmonics:
            continue
        h = k * clk
        if abs(f - h) <= cfg.tolerance * h and (best is None or abs(f - h) < abs(f - best[0])):
            best = (h, clk, k)
    return best


def _plane_copper(ctx: ErcContext, layer: str) -> dict[str, shapely.geometry.base.BaseGeometry]:
    geo = ctx.geometry()
    return {
        n: mp
        for n, mp in geo.copper_by_net(layer).items()
        if n is not None and ctx.net_kind(n) in ("power", "ground")
    }
