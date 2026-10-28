# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Power-delivery rule PD-001 (decoupling capacitor distance)."""

from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np
from pydantic import Field

from oa_erc.base import C0, ErcContext, ErcRule, RuleConfig, mean_dk
from oa_erc.remediation import TypedAction, TypedRemediation
from oa_results.violations import Violation


class PD001Config(RuleConfig):
    f_band: float = Field(
        default=1e9,
        gt=0,
        description="Upper frequency [Hz] of the capacitor's useful band; the distance limit is "
        "lambda/20 at this frequency (research 07 E6).",
    )
    max_distance: float | None = Field(
        default=None,
        gt=0,
        description="Distance limit [m]; None = lambda/20 = c / (20 f_band sqrt(Dk)).",
    )
    max_decap_pads: int = Field(
        default=2, ge=2, description="Components with at most this many pads are decap candidates."
    )
    min_load_pads: int = Field(
        default=3, ge=3, description="Components with at least this many pads are ICs (loads)."
    )


class DecapDistance(ErcRule):
    id = "PD-001"
    title = "Decoupling capacitor too far from the IC power pin"
    category = "power"
    aliases = ("E6",)
    default_severity = "warning"
    references = (
        "docs/research/07-quasistatic-thermal-emi.md section 3.1 (E6: radius <= lambda/20)",
        "docs/research/06-landscape-architecture.md section 3.2 (PD-001)",
    )
    Config = PD001Config
    explanation = (
        "A decoupling capacitor only supplies current in time when its loop to the IC is short "
        "(E6). A capacitor is a 2-pad component between a power and a ground net; its distance "
        "is measured from the component origin to the nearest pad of an IC (component with "
        "`min_load_pads` or more pads) on the same rail. The straight-line distance is a lower "
        "bound of the loop path (pin, via, plane, via, cap)."
    )

    def check(self, ctx: ErcContext, cfg: PD001Config) -> Iterable[Violation]:
        board = ctx.board
        limit = cfg.max_distance
        if limit is None:
            limit = C0 / (20 * cfg.f_band * math.sqrt(mean_dk(board)))
        pads = {p.id: p for p in board.pads}
        loads: dict[str, list[tuple[float, float]]] = {}
        for c in board.components:
            if len(c.pads) < cfg.min_load_pads or c.dnp:
                continue
            for pid in c.pads:
                p = pads[pid]
                if p.net is not None and ctx.net_kind(p.net) == "power":
                    loads.setdefault(p.net, []).append(p.position)
        arrays = {k: np.array(v) for k, v in loads.items()}
        for c in sorted(board.components, key=lambda c: c.refdes):
            if len(c.pads) > cfg.max_decap_pads or len(c.pads) < 2 or c.dnp:
                continue
            nets = [pads[i].net for i in c.pads]
            kinds = [ctx.net_kind(n) for n in nets]
            if sorted(kinds) != ["ground", "power"]:
                continue
            rail = nets[kinds.index("power")]
            if rail is None:
                continue
            arr = arrays.get(rail)
            if arr is None:
                continue
            d = float(np.hypot(arr[:, 0] - c.position[0], arr[:, 1] - c.position[1]).min())
            if d <= limit:
                continue
            yield self.violation(
                message=f"Decoupling capacitor {c.refdes} on {rail} is {d * 1e3:.1f} mm from the "
                f"nearest IC power pin (limit {limit * 1e3:.1f} mm).",
                location=c.position,
                nets=(rail,),
                layers=("L1" if c.side == "top" else board.stackup.layers[-1].id,),
                items=tuple(c.pads),
                measured=d,
                limit=limit,
                unit="m",
                remediation=TypedRemediation(
                    summary=f"Move {c.refdes} within {limit * 1e3:.1f} mm of an IC {rail} pin.",
                    actions=(
                        TypedAction(
                            kind="move_decap",
                            description=f"Place {c.refdes} next to the {rail} pins it serves.",
                            targets=(c.refdes, rail),
                            parameter="distance",
                            current_value=d,
                            suggested_value=limit,
                            unit="m",
                            applies_via="board_edit",
                            confidence="high",
                        ),
                        TypedAction(
                            kind="add_decap",
                            description=f"Add a capacitor near the {rail} pins instead.",
                            targets=(rail,),
                            applies_via="board_edit",
                            confidence="low",
                        ),
                    ),
                    evidence={"f_band_hz": cfg.f_band},
                ),
            )
