# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Crosstalk rule XT-001 (parallel coupled length, 3W rule)."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable

import numpy as np
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
        net_id: dict[str, int] = {}
        for layer in sorted(by_layer):
            items = by_layer[layer]
            lines = np.empty(len(items), dtype=object)
            lines[:] = [it[3] for it in items]
            nid = np.array([net_id.setdefault(it[0], len(net_id)) for it in items], dtype=np.intp)
            pids = {d: k for k, d in enumerate(sorted(set(pair_of.values())))}
            pid = np.array([pids[pair_of[it[0]]] if it[0] in pair_of else -1 for it in items])
            wid = np.array([it[2] for it in items])
            tree = STRtree(lines)
            acc: dict[tuple[str, str], list[tuple[float, tuple[float, float], float, str, str]]]
            acc = defaultdict(list)
            # candidate pairs in bulk: bbox query (the envelope of the round buffer is the
            # bounds grown by the radius), then the cheap filters as array operations
            rr = (cfg.spacing_factor * wid * 2)[:, None]
            bnd = shapely.bounds(lines)
            boxes = shapely.box(
                bnd[:, 0] - rr[:, 0],
                bnd[:, 1] - rr[:, 0],
                bnd[:, 2] + rr[:, 0],
                bnd[:, 3] + rr[:, 0],
            )
            qi, ti = tree.query(boxes)
            order = np.argsort(qi, kind="stable")
            qi, ti = qi[order], ti[order]
            keep = (ti > qi) & (nid[qi] != nid[ti]) & ~((pid[qi] >= 0) & (pid[qi] == pid[ti]))
            qi, ti = qi[keep], ti[keep]
            co = shapely.get_coordinates(lines).reshape(-1, 2, 2)
            dv = co[:, 1, :] - co[:, 0, :]
            nrm = np.sqrt(dv[:, 0] * dv[:, 0] + dv[:, 1] * dv[:, 1])
            dirs = np.where(nrm[:, None] > 0, dv / np.where(nrm > 0, nrm, 1.0)[:, None], dv)
            cosv = np.abs(dirs[qi, 0] * dirs[ti, 0] + dirs[qi, 1] * dirs[ti, 1])
            keep = ~(cosv < cos_min)
            qi, ti = qi[keep], ti[keep]
            if len(qi):
                cc = cfg.spacing_factor * np.maximum(wid[qi], wid[ti])
                zone = shapely.buffer(lines[ti], cc, cap_style="flat")
                inter = shapely.intersection(lines[qi], zone)
                length = shapely.length(inter)
                pos = length > 0
                qi, ti, inter, length = qi[pos], ti[pos], inter[pos], length[pos]
                gap = np.maximum(
                    shapely.distance(lines[qi], lines[ti]) - (wid[qi] + wid[ti]) / 2, 0.0
                )
                cen = shapely.get_coordinates(shapely.centroid(inter))
                for m, (i, j) in enumerate(zip(qi.tolist(), ti.tolist(), strict=True)):
                    net, sid = items[i][0], items[i][1]
                    net2, sid2 = items[j][0], items[j][1]
                    key = (min(net, net2), max(net, net2))
                    acc[key].append(
                        (
                            float(length[m]),
                            (float(cen[m, 0]), float(cen[m, 1])),
                            float(gap[m]),
                            sid,
                            sid2,
                        )
                    )
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
