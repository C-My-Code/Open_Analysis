# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""RF rule RF-001 (via fence pitch)."""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable

import numpy as np
from pydantic import Field

from oa_erc.base import C0, ErcContext, ErcRule, RuleConfig, eps_eff, track_id
from oa_erc.remediation import TypedAction, TypedRemediation
from oa_results.violations import Violation


class RF001Config(RuleConfig):
    f_max: float = Field(
        default=10e9,
        gt=0,
        description="Highest RF frequency [Hz] when the intent gives no f_max for the net "
        "(default 10 GHz, a typical upper CPWG design frequency).",
    )
    pitch_fraction: float = Field(
        default=0.1, gt=0, description="Pitch limit as a fraction of the wavelength (lambda/10)."
    )
    max_lateral: float = Field(
        default=3e-3,
        gt=0,
        description="Ground vias within this lateral distance [m] of the trace edge can form "
        "its fence.",
    )
    row_tolerance: float = Field(
        default=0.3e-3, gt=0, description="Lateral scatter [m] allowed within one via row."
    )
    min_fence_vias: int = Field(default=4, ge=3, description="Fewest vias that make a fence row.")


class FencePitch(ErcRule):
    id = "RF-001"
    title = "Via fence pitch along RF line"
    category = "rf"
    default_severity = "warning"
    references = (
        "docs/research/06-landscape-architecture.md section 3.2 (RF-001: lambda/10)",
        "eps_eff of the layer: inner Dk, outer (Dk + 1) / 2 (oa_erc.base.eps_eff)",
    )
    Config = RF001Config
    explanation = (
        "A ground via fence beside an RF line (CPWG) keeps the ground planes at one potential "
        "and suppresses parallel-plate modes only while the pitch is below about lambda/10 at "
        "the highest frequency. A fence is a row of at least `min_fence_vias` ground vias "
        "running along a trace within `max_lateral` of its edge; the largest gap in the row is "
        "compared with lambda/10."
    )

    def check(self, ctx: ErcContext, cfg: RF001Config) -> Iterable[Violation]:
        board = ctx.board
        gv = [v for v in board.vias if ctx.net_kind(v.net) == "ground"]
        if not gv:
            return
        pos = np.array([v.position for v in gv])
        geo = ctx.geometry()
        for net in ctx.nets_in_scope():
            best: tuple[float, tuple[float, float], str, list[str], str, float] | None = None
            for seg in geo.segments(net):
                (x0, y0), (x1, y1) = seg.seg.start, seg.seg.end
                ln = math.hypot(x1 - x0, y1 - y0)
                if ln < 3e-3 or seg.seg.kind != "line":
                    continue
                ux, uy = (x1 - x0) / ln, (y1 - y0) / ln
                rel = pos - np.array([x0, y0])
                t = rel[:, 0] * ux + rel[:, 1] * uy
                d = -rel[:, 0] * uy + rel[:, 1] * ux
                f = ctx.f_max(net, cfg.f_max)
                limit = cfg.pitch_fraction * C0 / (f * math.sqrt(eps_eff(board, seg.layer)))
                for sign in (1.0, -1.0):
                    lat = sign * d - seg.width / 2
                    sel = np.where((lat > 0) & (lat <= cfg.max_lateral) & (t >= 0) & (t <= ln))[0]
                    if sel.size < cfg.min_fence_vias:
                        continue
                    order = sel[np.argsort(lat[sel])]
                    groups: list[list[int]] = [[int(order[0])]]
                    for k in order[1:]:
                        if lat[int(k)] - lat[groups[-1][-1]] > cfg.row_tolerance:
                            groups.append([])
                        groups[-1].append(int(k))
                    for g in groups:
                        if len(g) < cfg.min_fence_vias:
                            continue
                        g.sort(key=lambda k: t[k])
                        gaps = [t[b] - t[a] for a, b in itertools.pairwise(g)]
                        i = int(np.argmax(gaps))
                        if gaps[i] > limit and (best is None or gaps[i] > best[0]):
                            a, b = g[i], g[i + 1]
                            mid = tuple(((pos[a] + pos[b]) / 2).tolist())
                            best = (
                                float(gaps[i]),
                                mid,
                                seg.layer,
                                [gv[a].id, gv[b].id],
                                track_id(seg.id),
                                limit,
                            )
            if best is None:
                continue
            gap, mid, layer, vids, tid, limit = best
            yield self.violation(
                message=f"Ground via fence along {net} on {layer} has a {gap * 1e3:.2f} mm pitch "
                f"(lambda/10 limit {limit * 1e3:.2f} mm).",
                location=mid,
                nets=(net,),
                layers=(layer,),
                items=(tid, *vids),
                measured=gap,
                limit=limit,
                unit="m",
                remediation=TypedRemediation(
                    summary=f"Reduce the via fence pitch along {net} to {limit * 1e3:.2f} mm or "
                    "less.",
                    actions=(
                        TypedAction(
                            kind="add_stitching_vias",
                            description=f"Add ground vias to the fence along {net}.",
                            targets=(net, *vids),
                            parameter="via_pitch",
                            current_value=gap,
                            suggested_value=limit,
                            unit="m",
                            applies_via="board_edit",
                            confidence="high",
                        ),
                    ),
                    evidence={"f_max_hz": ctx.f_max(net, cfg.f_max)},
                ),
            )
