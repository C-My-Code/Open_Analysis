# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Edge rules ED-001, ED-002, ED-003."""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np
import shapely
import shapely.ops
from pydantic import Field
from shapely.ops import nearest_points

from oa_erc.base import C0, ErcContext, ErcRule, RuleConfig, mean_dk, track_id
from oa_erc.refpath import net_runs, run_line
from oa_erc.remediation import TypedAction, TypedRemediation
from oa_model.padstack import CircleShape
from oa_results.violations import Violation

_E1 = (
    "docs/research/07-quasistatic-thermal-emi.md section 3.1 (E1: >= 3h or >= 1 mm; E5: "
    "s <= lambda/20 = c / (20 f sqrt(eps_eff)))",
    "docs/research/06-landscape-architecture.md section 3.2 (ED-001..003)",
)


def _outline_boundary(ctx: ErcContext) -> shapely.geometry.base.BaseGeometry:
    def build() -> shapely.geometry.base.BaseGeometry:
        return ctx.geometry().outline().boundary

    result: shapely.geometry.base.BaseGeometry = ctx.memo("outline_boundary", build)
    return result


class ED001Config(RuleConfig):
    edge_h_factor: float = Field(
        default=3.0,
        gt=0,
        description="Minimum distance of the trace body from a reference-plane edge, in units "
        "of the dielectric height h to the plane (3h rule, research 06 ED-001).",
    )
    min_length: float = Field(
        default=2e-3, gt=0, description="Shortest run [m] along the plane edge that is reported."
    )
    cov_ok: float = Field(default=0.8, ge=0.05, le=1.0, description="See RP-001 [fraction].")
    board_edge_band: float = Field(
        default=1e-3,
        ge=0,
        description="Runs closer than this [m] to the board outline are left to ED-002.",
    )


class NearPlaneEdge(ErcRule):
    id = "ED-001"
    title = "Trace too close to reference-plane edge"
    category = "edge"
    default_severity = "warning"
    references = _E1
    Config = ED001Config
    explanation = (
        "Near a plane edge the return current spreads over about 3h each side of the trace; "
        "when the plane ends inside that footprint the return path is cut off and the fringing "
        "field radiates. The rule reports runs where the plane covers less than `cov_ok` of the "
        "footprint over a stretch (not a short antipad dip) and the plane edge is closer than "
        "`edge_h_factor * h`. Runs near the board outline are reported by ED-002."
    )

    def check(self, ctx: ErcContext, cfg: ED001Config) -> Iterable[Violation]:
        boundary = _outline_boundary(ctx)
        for net in ctx.nets_in_scope():
            for run in net_runs(ctx, net, cfg.cov_ok):
                if run.status != "partial" or run.length < cfg.min_length:
                    continue
                if (
                    run.prev is not None
                    and run.next is not None
                    and run.prev.status == "good"
                    and run.next.status == "good"
                ):
                    continue  # a short dip is an antipad (RP-002)
                if run_line(run).distance(boundary) < cfg.board_edge_band:
                    continue
                refs = [i.ref for i in run.intervals if i.ref is not None]
                h = min(r.distance for r in refs)
                edge = min(r.edge_distance for r in refs)
                limit = cfg.edge_h_factor * h
                if edge >= limit:
                    continue
                yield self.violation(
                    message=f"Net {net} runs {run.length * 1e3:.1f} mm along a reference-plane "
                    f"edge on {run.layer} ({edge * 1e3:.2f} mm from the edge).",
                    location=run.midpoint(),
                    nets=(net,),
                    layers=(run.layer,),
                    items=tuple(dict.fromkeys(track_id(s) for s in run.seg_ids)),
                    measured=edge,
                    limit=limit,
                    unit="m",
                    remediation=TypedRemediation(
                        summary=f"Move {net} at least {limit * 1e3:.2f} mm inside the plane edge "
                        "or extend the plane under it.",
                        actions=(
                            TypedAction(
                                kind="move",
                                description=f"Move {net} away from the plane edge.",
                                targets=(net, run.layer),
                                parameter="edge_distance",
                                current_value=edge,
                                suggested_value=limit,
                                unit="m",
                                applies_via="board_edit",
                                confidence="high",
                            ),
                        ),
                        evidence={"coverage_min": min(r.coverage for r in refs), "h_m": h},
                    ),
                )


class ED002Config(RuleConfig):
    min_distance: float = Field(
        default=1e-3,
        gt=0,
        description="Minimum distance [m] from the trace/via edge to the board outline "
        "(E1: >= 1 mm).",
    )
    h_factor: float = Field(
        default=3.0,
        ge=0,
        description="The limit is max(min_distance, h_factor * h) with h the height above the "
        "reference plane [m] (E1: >= 3h).",
    )
    check_vias: bool = Field(default=True, description="Also check vias of the nets in scope.")
    launch_length: float = Field(
        default=0.0,
        ge=0,
        description="Extra length [m] ignored at a trace end that touches the board outline "
        "(edge launch to a connector), on top of the distance limit and the track width.",
    )


class NearBoardEdge(ErcRule):
    id = "ED-002"
    title = "High-speed trace/via near board edge"
    category = "edge"
    aliases = ("E1",)
    default_severity = "warning"
    references = _E1
    Config = ED002Config
    explanation = (
        "Traces near the board edge have fringing fields that couple to the outside and a "
        "pulled-back plane; they radiate more and are more sensitive to ESD (E1). The limit is "
        "the larger of `min_distance` and `h_factor * h`."
    )

    def check(self, ctx: ErcContext, cfg: ED002Config) -> Iterable[Violation]:
        boundary = _outline_boundary(ctx)
        geo = ctx.geometry()
        for net in ctx.nets_in_scope():
            h_by_seg: dict[str, float] = {}
            for run in net_runs(ctx, net):
                for i in run.intervals:
                    if i.ref is not None:
                        h_by_seg[i.seg.id] = min(h_by_seg.get(i.seg.id, math.inf), i.ref.distance)
            segs = geo.segments(net)
            dist = _edge_distances(boundary, segs)
            for seg, (d_start, d_end, d_line) in zip(segs, dist, strict=True):
                limit = max(cfg.min_distance, cfg.h_factor * h_by_seg.get(seg.id, 0.0))
                if d_start <= seg.width / 2 + 1e-6 or d_end <= seg.width / 2 + 1e-6:
                    # a trace end touches the outline: trim the edge launch
                    line = _trim_launch(
                        shapely.LineString([seg.seg.start, seg.seg.end]),
                        boundary,
                        seg.width,
                        limit + seg.width + cfg.launch_length,
                    )
                    if line is None:
                        continue
                    d = boundary.distance(line) - seg.width / 2
                else:
                    line = shapely.LineString([seg.seg.start, seg.seg.end])
                    d = d_line - seg.width / 2
                if d >= limit:
                    continue
                p = nearest_points(line, boundary)[0]
                yield self._report(net, seg.layer, track_id(seg.id), (p.x, p.y), d, limit, "trace")
            if cfg.check_vias:
                for v in ctx.board.vias:
                    if v.net != net:
                        continue
                    pt = shapely.Point(v.position)
                    d = boundary.distance(pt) - _via_radius(ctx, v.padstack)
                    if d < cfg.min_distance:
                        yield self._report(
                            net, v.start_layer, v.id, v.position, d, cfg.min_distance, "via"
                        )

    def _report(
        self,
        net: str,
        layer: str,
        item: str,
        loc: tuple[float, float],
        d: float,
        limit: float,
        what: str,
    ) -> Violation:
        d = max(d, 0.0)
        return self.violation(
            message=f"{what.capitalize()} of {net} on {layer} is {d * 1e3:.2f} mm from the board "
            f"edge (limit {limit * 1e3:.2f} mm).",
            location=loc,
            nets=(net,),
            layers=(layer,),
            items=(item,),
            measured=d,
            limit=limit,
            unit="m",
            remediation=TypedRemediation(
                summary=f"Keep {net} at least {limit * 1e3:.2f} mm from the board edge.",
                actions=(
                    TypedAction(
                        kind="move",
                        description=f"Move the {what} of {net} away from the board edge.",
                        targets=(item,),
                        parameter="edge_clearance",
                        current_value=d,
                        suggested_value=limit,
                        unit="m",
                        applies_via="board_edit",
                        confidence="high",
                    ),
                    TypedAction(
                        kind="change_rule",
                        description="Add a KiCad edge_clearance rule for this net class so DRC "
                        "enforces the distance.",
                        targets=(net,),
                        parameter="edge_clearance",
                        suggested_value=limit,
                        unit="m",
                        applies_via="dru_rule",
                        confidence="medium",
                    ),
                ),
                evidence={"distance_m": d},
            ),
        )


def _edge_distances(
    boundary: shapely.geometry.base.BaseGeometry, segs: Sequence[Any]
) -> list[tuple[float, float, float]]:
    """(distance of the start, of the end, of the whole straight segment) to ``boundary`` for
    every segment, in three array calls instead of per-segment shapely objects."""
    if not segs:
        return []
    xy = np.array([(*s.seg.start, *s.seg.end) for s in segs], dtype=float)
    a = shapely.distance(boundary, shapely.points(xy[:, 0:2]))
    b = shapely.distance(boundary, shapely.points(xy[:, 2:4]))
    lines = shapely.linestrings(xy.reshape(-1, 2, 2))
    c = shapely.distance(boundary, lines)
    return list(zip(a.tolist(), b.tolist(), c.tolist(), strict=True))


def _trim_launch(
    line: shapely.LineString,
    boundary: shapely.geometry.base.BaseGeometry,
    width: float,
    trim: float,
) -> shapely.LineString | None:
    """Drop ``trim`` metres at each end of ``line`` that touches the outline (edge launch)."""
    pts = list(line.coords)
    start, end = 0.0, line.length
    if boundary.distance(shapely.Point(pts[0])) <= width / 2 + 1e-6:
        start = trim
    if boundary.distance(shapely.Point(pts[-1])) <= width / 2 + 1e-6:
        end = line.length - trim
    if start == 0.0 and end == line.length:
        return line
    if end - start <= 1e-9:
        return None
    out = shapely.ops.substring(line, start, end)
    return out if isinstance(out, shapely.LineString) else None


def _via_radius(ctx: ErcContext, padstack: str) -> float:
    for ps in ctx.board.padstacks:
        if ps.id == padstack:
            for pl in ps.layers:
                if isinstance(pl.pad, CircleShape):
                    return pl.pad.diameter / 2
    return 0.0


class ED003Config(RuleConfig):
    f_max: float = Field(
        default=1e9,
        gt=0,
        description="Highest frequency [Hz] the stitching must hold to (research 07 E5 example).",
    )
    edge_band: float = Field(
        default=3e-3,
        gt=0,
        description="Ground vias within this distance [m] of the board outline form the edge "
        "stitching fence.",
    )
    min_fence_vias: int = Field(default=3, ge=2, description="Fewest vias that make a fence.")
    link_factor: float = Field(
        default=3.0,
        gt=1.0,
        description="Neighbouring vias farther apart than link_factor * limit are not one fence.",
    )


class StitchSpacing(ErcRule):
    id = "ED-003"
    title = "Stitching via spacing > lambda/20"
    category = "edge"
    aliases = ("E5",)
    default_severity = "warning"
    references = _E1
    Config = ED003Config
    explanation = (
        "A ground via fence only shields up to the frequency where its pitch is lambda/20, "
        "lambda = c / (f sqrt(eps_eff)) (E5). The rule reports gaps larger than lambda/20 in a "
        "row of ground vias along the board outline. Fences along plane edges and RF lines are "
        "future work (RF lines: RF-001)."
    )

    def check(self, ctx: ErcContext, cfg: ED003Config) -> Iterable[Violation]:
        boundary = _outline_boundary(ctx)
        limit = C0 / (20 * cfg.f_max * math.sqrt(mean_dk(ctx.board)))
        near = []
        for v in ctx.board.vias:
            if ctx.net_kind(v.net) != "ground":
                continue
            p = shapely.Point(v.position)
            if boundary.distance(p) <= cfg.edge_band:
                near.append((boundary.project(p), v))
        near.sort(key=lambda t: (t[0], t[1].id))
        chains: list[list] = []  # type: ignore[type-arg]
        for s, v in near:
            if chains and math.dist(chains[-1][-1][1].position, v.position) <= (
                cfg.link_factor * limit
            ):
                chains[-1].append((s, v))
            else:
                chains.append([(s, v)])
        for chain in chains:
            if len(chain) < cfg.min_fence_vias:
                continue
            for (_, a), (_, b) in itertools.pairwise(chain):
                gap = math.dist(a.position, b.position)
                if gap <= limit:
                    continue
                mid = ((a.position[0] + b.position[0]) / 2, (a.position[1] + b.position[1]) / 2)
                yield self.violation(
                    message=f"Edge stitching vias {a.id} and {b.id} are {gap * 1e3:.2f} mm apart "
                    f"(lambda/20 at {cfg.f_max / 1e9:g} GHz is {limit * 1e3:.2f} mm).",
                    location=mid,
                    nets=(a.net,) if a.net else (),
                    layers=(a.start_layer,),
                    items=(a.id, b.id),
                    measured=gap,
                    limit=limit,
                    unit="m",
                    remediation=TypedRemediation(
                        summary=f"Add stitching vias so the pitch is at most {limit * 1e3:.2f} mm.",
                        actions=(
                            TypedAction(
                                kind="add_stitching_vias",
                                description="Add ground vias between the two vias.",
                                targets=(a.id, b.id),
                                parameter="via_pitch",
                                current_value=gap,
                                suggested_value=limit,
                                unit="m",
                                applies_via="board_edit",
                                confidence="high",
                            ),
                        ),
                        evidence={"f_max_hz": cfg.f_max},
                    ),
                )
