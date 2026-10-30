# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Crosstalk rule XT-001 (parallel coupled length, 3W rule)."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable

import shapely
from pydantic import Field
from shapely import STRtree

from oa_erc.base import ErcContext, ErcRule, RuleConfig, track_id
from oa_erc.remediation import TypedAction, TypedRemediation
from oa_results.violations import Violation


class XT001Config(RuleConfig):
    spacing_factor: float = Field(
        default=3.0,
        gt=0,
        description="Centre-to-centre spacing below this multiple of the wider track width W "
        "counts as coupled (the 3W rule, research 07 E8).",
    )
    max_length: float = Field(
        default=10e-3, gt=0, description="Longest parallel coupled length [m] allowed."
    )
    max_angle_deg: float = Field(
        default=15.0, gt=0, description="Segments are 'parallel' within this angle [degrees]."
    )


class ParallelCoupling(ErcRule):
    id = "XT-001"
    title = "Parallel coupled length at spacing"
    category = "crosstalk"
    aliases = ("E8",)
    default_severity = "warning"
    references = (
        "docs/research/07-quasistatic-thermal-emi.md section 3.1 (E8: spacing >= 3W)",
        "docs/research/06-landscape-architecture.md section 3.2 (XT-001)",
        "Engineering default: max_length 10 mm is a design-guide value (configurable)",
    )
    Config = XT001Config
    explanation = (
        "Neighbouring traces couple capacitively and inductively; the coupled noise grows with "
        "the parallel length until the saturation length. For every pair of nets on a layer the "
        "length over which they run parallel closer than `spacing_factor * W` is summed; pairs "
        "of one differential pair are skipped. Broadside coupling between layers is future work."
    )

    def check(self, ctx: ErcContext, cfg: XT001Config) -> Iterable[Violation]:
        geo = ctx.geometry()
        scope = ctx.nets_in_scope()
        pair_of = {}
        for d in ctx.board.diff_pairs:
            pair_of[d.p_net] = d.id
            pair_of[d.n_net] = d.id
        by_layer: dict[str, list[tuple[str, str, float, shapely.LineString]]] = defaultdict(list)
        for net in scope:
            for s in geo.segments(net):
                if s.length <= 0:
                    continue
                by_layer[s.layer].append(
                    (net, s.id, s.width, shapely.LineString([s.seg.start, s.seg.end]))
                )
        cos_min = math.cos(math.radians(cfg.max_angle_deg))
        for layer in sorted(by_layer):
            items = by_layer[layer]
            lines = [it[3] for it in items]
            tree = STRtree(lines)
            acc: dict[tuple[str, str], list[tuple[float, tuple[float, float], float, str, str]]]
            acc = defaultdict(list)
            for i, (net, sid, w, ln) in enumerate(items):
                reach = cfg.spacing_factor * w
                # bbox query: the envelope of the round buffer is the bounds grown by the radius
                bx0, by0, bx1, by1 = ln.bounds
                rr = reach * 2
                for j in tree.query(shapely.box(bx0 - rr, by0 - rr, bx1 + rr, by1 + rr)):
                    j = int(j)
                    if j <= i:
                        continue
                    net2, sid2, w2, ln2 = items[j]
                    if net2 == net or (net in pair_of and pair_of[net] == pair_of.get(net2)):
                        continue
                    if abs(_dir(ln) @ _dir(ln2)) < cos_min:
                        continue
                    cc = cfg.spacing_factor * max(w, w2)
                    zone = ln2.buffer(cc, cap_style="flat")
                    inter = ln.intersection(zone)
                    length = inter.length
                    if length <= 0:
                        continue
                    edge_gap = max(ln.distance(ln2) - (w + w2) / 2, 0.0)
                    c = inter.centroid
                    key = (min(net, net2), max(net, net2))
                    acc[key].append((length, (c.x, c.y), edge_gap, sid, sid2))
            for (a, b), parts in sorted(acc.items()):
                total = sum(p[0] for p in parts)
                if total <= cfg.max_length:
                    continue
                best = max(parts, key=lambda p: p[0])
                gap = min(p[2] for p in parts)
                yield self.violation(
                    message=f"Nets {a} and {b} run parallel for {total * 1e3:.1f} mm on {layer} "
                    f"at {gap * 1e3:.2f} mm spacing (limit {cfg.max_length * 1e3:.1f} mm).",
                    location=best[1],
                    nets=(a, b),
                    layers=(layer,),
                    items=tuple(dict.fromkeys(track_id(s) for p in parts for s in p[3:])),
                    measured=total,
                    limit=cfg.max_length,
                    unit="m",
                    remediation=TypedRemediation(
                        summary=f"Increase the spacing of {a} and {b} to at least "
                        f"{cfg.spacing_factor:g}W centre to centre or shorten the parallel run.",
                        actions=(
                            TypedAction(
                                kind="change_spacing",
                                description=f"Move {a} and {b} further apart.",
                                targets=(a, b, layer),
                                parameter="spacing",
                                current_value=gap,
                                unit="m",
                                applies_via="board_edit",
                                confidence="medium",
                            ),
                            TypedAction(
                                kind="change_rule",
                                description="Enforce with a KiCad clearance rule between the "
                                "net classes.",
                                targets=(a, b),
                                parameter="clearance",
                                unit="m",
                                applies_via="dru_rule",
                                confidence="low",
                            ),
                        ),
                        evidence={"min_edge_spacing_m": gap, "segments": len(parts)},
                    ),
                )


def _dir(line: shapely.LineString):  # type: ignore[no-untyped-def]
    import numpy as np

    (x0, y0), (x1, y1) = line.coords[0], line.coords[-1]
    v = np.array([x1 - x0, y1 - y0])
    n = np.linalg.norm(v)
    return v / n if n else v
