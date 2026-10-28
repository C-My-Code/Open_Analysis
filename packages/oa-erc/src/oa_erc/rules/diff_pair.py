# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Differential-pair rules DP-001, DP-002, DP-003."""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
import shapely
from pydantic import Field

from oa_erc.base import C0, ErcContext, ErcRule, RuleConfig, eps_eff, mean_dk, seg_point, track_id
from oa_erc.remediation import TypedAction, TypedRemediation
from oa_results.violations import Violation

_REFS = (
    "docs/research/06-landscape-architecture.md section 3.2 (DP-001..003)",
    "Engineering defaults: breakout allowance and skew limits are design-guide values "
    "(configurable), not standard limits",
)

STEP = 1e-4
"""Sampling step along the pair [m]."""


@dataclass
class _Sample:
    layer: str
    seg_id: str
    pos: tuple[float, float]
    gap: float  # edge-to-edge gap to the partner on the same layer [m]


def _pairs(ctx: ErcContext) -> list[tuple[str, str, str]]:
    scope = set(ctx.nets_in_scope())
    out = []
    for d in ctx.board.diff_pairs:
        if d.p_net in scope or d.n_net in scope:
            out.append((d.id, d.p_net, d.n_net))
    return sorted(out)


def _profile(ctx: ErcContext, p: str, n: str) -> list[_Sample]:
    def build() -> list[_Sample]:
        geo = ctx.geometry()
        n_lines: dict[str, list[shapely.LineString]] = {}
        n_w: dict[str, float] = {}
        for s in geo.segments(n):
            n_lines.setdefault(s.layer, []).append(shapely.LineString([s.seg.start, s.seg.end]))
            n_w[s.layer] = s.width
        union = {k: shapely.union_all(v) for k, v in n_lines.items()}
        out: list[_Sample] = []
        for s in geo.segments(p):
            line = shapely.LineString([s.seg.start, s.seg.end])
            ts = np.arange(0.0, s.length, STEP) + STEP / 2
            pts = shapely.line_interpolate_point(line, ts)
            u = union.get(s.layer)
            wn = n_w.get(s.layer, s.width)
            gaps = (
                shapely.distance(pts, u) - (s.width + wn) / 2
                if u is not None
                else np.full(len(ts), 1.0)
            )
            for pt, g in zip(pts, gaps, strict=True):
                out.append(_Sample(s.layer, s.id, (pt.x, pt.y), float(g)))
        return out

    result: list[_Sample] = ctx.memo(("dp_profile", p, n), build)
    return result


def _runs(samples: list[_Sample], pred: object) -> Iterable[list[_Sample]]:
    cur: list[_Sample] = []
    for s in samples:
        if pred(s) and (not cur or cur[-1].layer == s.layer):  # type: ignore[operator]
            cur.append(s)
            continue
        if cur:
            yield cur
            cur = []
        if pred(s):  # type: ignore[operator]
            cur.append(s)
    if cur:
        yield cur


def _nominal(samples: list[_Sample], layer: str) -> float:
    gaps = [s.gap for s in samples if s.layer == layer and s.gap < 1.0]
    return statistics.median(gaps) if gaps else 0.0


class DP001Config(RuleConfig):
    max_uncoupled: float = Field(
        default=2e-3,
        gt=0,
        description="Longest uncoupled section [m] allowed (breakout allowance).",
    )
    uncoupled_gap_factor: float = Field(
        default=3.0,
        gt=1,
        description="Gap larger than this multiple of the nominal gap counts as uncoupled.",
    )


class UncoupledLength(ErcRule):
    id = "DP-001"
    title = "Uncoupled length of a differential pair"
    category = "diff_pair"
    default_severity = "warning"
    references = _REFS
    Config = DP001Config
    explanation = (
        "Where the pair members spread apart the differential impedance rises and common-mode "
        "conversion starts. The nominal gap per layer is the median gap along the pair; a section "
        "with a gap above `uncoupled_gap_factor` times nominal is uncoupled. Sections longer "
        "than `max_uncoupled` are reported."
    )

    def check(self, ctx: ErcContext, cfg: DP001Config) -> Iterable[Violation]:
        for _pid, p, n in _pairs(ctx):
            prof = _profile(ctx, p, n)
            nom = {lyr: _nominal(prof, lyr) for lyr in {s.layer for s in prof}}
            for run in _runs(
                prof,
                lambda s, nom=nom: s.gap > cfg.uncoupled_gap_factor * max(nom[s.layer], 1e-6),
            ):
                length = len(run) * STEP
                if length <= cfg.max_uncoupled:
                    continue
                mid = run[len(run) // 2]
                yield self.violation(
                    message=f"Pair {p}/{n} is uncoupled for {length * 1e3:.1f} mm on "
                    f"{mid.layer} (limit {cfg.max_uncoupled * 1e3:.1f} mm).",
                    location=mid.pos,
                    nets=(p, n),
                    layers=(mid.layer,),
                    items=tuple(dict.fromkeys(track_id(s.seg_id) for s in run)),
                    measured=length,
                    limit=cfg.max_uncoupled,
                    unit="m",
                    remediation=TypedRemediation(
                        summary=f"Route {p}/{n} coupled over the whole run or shorten the "
                        f"breakout to {cfg.max_uncoupled * 1e3:.1f} mm.",
                        actions=(
                            TypedAction(
                                kind="reroute",
                                description=f"Bring {p} and {n} together over the uncoupled run.",
                                targets=(p, n),
                                parameter="uncoupled_length",
                                current_value=length,
                                suggested_value=cfg.max_uncoupled,
                                unit="m",
                                applies_via="board_edit",
                            ),
                            TypedAction(
                                kind="change_rule",
                                description="Enforce with a KiCad diff_pair_uncoupled rule.",
                                targets=(p, n),
                                parameter="diff_pair_uncoupled",
                                suggested_value=cfg.max_uncoupled,
                                unit="m",
                                applies_via="dru_rule",
                            ),
                        ),
                        evidence={"nominal_gap_m": nom[mid.layer]},
                    ),
                )


class DP002Config(RuleConfig):
    max_skew: float = Field(
        default=1e-12,
        gt=0,
        description="Largest intra-pair delay difference [s] (about 5 mil in FR-4 microstrip).",
    )


def _net_delay(ctx: ErcContext, net: str) -> tuple[float, float]:
    """(length [m], delay [s]) of a net: routed segments plus via barrels."""
    geo = ctx.geometry()
    board = ctx.board
    total = 0.0
    delay = 0.0
    for s in geo.segments(net):
        total += s.length
        delay += s.length * math.sqrt(eps_eff(board, s.layer)) / C0
    layers = {lyr.id: lyr for lyr in board.stackup.layers}
    for v in board.vias:
        if v.net == net:
            ln = layers[v.start_layer].z_top - layers[v.end_layer].z_bottom
            total += ln
            delay += ln * math.sqrt(mean_dk(board)) / C0
    return total, delay


class IntraPairSkew(ErcRule):
    id = "DP-002"
    title = "Intra-pair skew"
    category = "diff_pair"
    default_severity = "warning"
    references = _REFS
    Config = DP002Config
    explanation = (
        "Unequal member delays convert differential signal to common mode. The delay of each "
        "net is the sum of segment length * sqrt(eps_eff) / c, with eps_eff of the layer "
        "(inner: Dk; outer: (Dk + 1) / 2) plus the via barrels."
    )

    def check(self, ctx: ErcContext, cfg: DP002Config) -> Iterable[Violation]:
        for _pid, p, n in _pairs(ctx):
            lp, dp = _net_delay(ctx, p)
            ln, dn = _net_delay(ctx, n)
            skew = abs(dp - dn)
            if skew <= cfg.max_skew or (lp == 0 and ln == 0):
                continue
            longer, shorter = (p, n) if lp > ln else (n, p)
            segs = ctx.geometry().segments(longer)
            mid = segs[len(segs) // 2]
            yield self.violation(
                message=f"Pair {p}/{n} has {skew * 1e12:.2f} ps intra-pair skew "
                f"({abs(lp - ln) * 1e3:.2f} mm length mismatch).",
                location=seg_point(mid.seg, mid.length / 2),
                nets=(p, n),
                layers=tuple(dict.fromkeys(s.layer for s in ctx.geometry().segments(p))),
                items=(),
                measured=skew,
                limit=cfg.max_skew,
                unit="s",
                remediation=TypedRemediation(
                    summary=f"Lengthen {shorter} by {abs(lp - ln) * 1e3:.2f} mm or shorten "
                    f"{longer} to match the pair.",
                    actions=(
                        TypedAction(
                            kind="reroute",
                            description=f"Add serpentine length to {shorter}.",
                            targets=(shorter,),
                            parameter="length",
                            current_value=min(lp, ln),
                            suggested_value=max(lp, ln),
                            unit="m",
                            applies_via="board_edit",
                            confidence="high",
                        ),
                        TypedAction(
                            kind="change_rule",
                            description="Enforce with a KiCad skew rule.",
                            targets=(p, n),
                            parameter="skew",
                            suggested_value=cfg.max_skew,
                            unit="s",
                            applies_via="dru_rule",
                            confidence="low",
                        ),
                    ),
                    evidence={"length_mismatch_m": abs(lp - ln)},
                ),
            )


class DP003Config(RuleConfig):
    gap_tolerance: float = Field(
        default=0.25,
        gt=0,
        description="Allowed relative deviation [fraction] of the gap from the layer's median.",
    )
    min_length: float = Field(
        default=2e-3,
        gt=0,
        description="Shortest section [m] with a deviating gap that is reported.",
    )
    uncoupled_gap_factor: float = Field(
        default=3.0, gt=1, description="Gaps above this multiple of nominal are DP-001's."
    )


class GapVariation(ErcRule):
    id = "DP-003"
    title = "Gap variation along the coupled section"
    category = "diff_pair"
    default_severity = "warning"
    references = _REFS
    Config = DP003Config
    explanation = (
        "A pair whose spacing changes along the coupled section has a varying differential "
        "impedance. Sections longer than `min_length` whose gap differs from the layer's median "
        "gap by more than `gap_tolerance` (but is still coupled) are reported."
    )

    def check(self, ctx: ErcContext, cfg: DP003Config) -> Iterable[Violation]:
        for _pid, p, n in _pairs(ctx):
            prof = _profile(ctx, p, n)
            nom = {lyr: _nominal(prof, lyr) for lyr in {s.layer for s in prof}}

            def deviating(s: _Sample, nom: dict[str, float] = nom) -> bool:
                m = max(nom[s.layer], 1e-6)
                return (
                    abs(s.gap - m) > cfg.gap_tolerance * m and s.gap <= cfg.uncoupled_gap_factor * m
                )

            for run in _runs(prof, deviating):
                length = len(run) * STEP
                if length < cfg.min_length:
                    continue
                layer = run[0].layer
                m = nom[layer]
                worst = max(run, key=lambda s: abs(s.gap - m))
                mid = run[len(run) // 2]
                yield self.violation(
                    message=f"Gap of {p}/{n} on {layer} is {worst.gap * 1e3:.2f} mm instead of "
                    f"{m * 1e3:.2f} mm for {length * 1e3:.1f} mm.",
                    location=mid.pos,
                    nets=(p, n),
                    layers=(layer,),
                    items=tuple(dict.fromkeys(track_id(s.seg_id) for s in run)),
                    measured=worst.gap,
                    limit=m * (1 + cfg.gap_tolerance)
                    if worst.gap > m
                    else m * (1 - cfg.gap_tolerance),
                    unit="m",
                    remediation=TypedRemediation(
                        summary=f"Keep the gap of {p}/{n} at {m * 1e3:.2f} mm along the section.",
                        actions=(
                            TypedAction(
                                kind="change_spacing",
                                description=f"Restore the nominal gap of {p}/{n}.",
                                targets=(p, n, layer),
                                parameter="diff_pair_gap",
                                current_value=worst.gap,
                                suggested_value=m,
                                unit="m",
                                applies_via="board_edit",
                                confidence="high",
                            ),
                            TypedAction(
                                kind="change_rule",
                                description="Enforce with a KiCad diff_pair_gap rule.",
                                targets=(p, n),
                                parameter="diff_pair_gap",
                                suggested_value=m,
                                unit="m",
                                applies_via="dru_rule",
                            ),
                        ),
                        evidence={"nominal_gap_m": m, "section_length_m": length},
                    ),
                )
