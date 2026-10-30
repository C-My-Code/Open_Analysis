# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Return-path analysis on top of ``oa_geometry.ReferenceMap`` shared by the RP/ED rules.

A trace span is classified against the *primary reference layer* of each side: the nearest
conductor layer on that side that carries reference copper (ground/power nets) over at least
``MIN_PLANE_FRACTION`` of the board outline.

- ``good``: a reference on the primary layer covers at least ``cov_ok`` of the footprint;
- ``partial``: the primary layer is present but covers less (plane edge, antipad cluster);
- ``void``: no reference on the primary layer (gap, slot, missing plane, or no plane layer).

A span is ``good`` when any side is good, ``partial`` when none is good but one is partial.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

import shapely

from oa_erc.base import ErcContext, seg_point
from oa_geometry import ReferenceMap, RefInfo, RoutedSegment
from oa_model.common import Vec2

MIN_PLANE_FRACTION = 0.2
JOIN_TOL = 2e-6


@dataclass
class Interval:
    seg: RoutedSegment
    s0: float
    s1: float
    status: str
    ref: RefInfo | None
    refs: tuple[RefInfo, ...]
    p0: Vec2
    p1: Vec2

    @property
    def length(self) -> float:
        return self.s1 - self.s0

    @property
    def refs_key(self) -> frozenset[str]:
        return frozenset(r.net for r in self.refs)


@dataclass
class Run:
    net: str
    layer: str
    status: str
    intervals: list[Interval] = field(default_factory=list)
    prev: Run | None = None
    next: Run | None = None

    @property
    def length(self) -> float:
        return sum(i.length for i in self.intervals)

    @property
    def ref_net(self) -> str | None:
        refs = [i for i in self.intervals if i.ref is not None]
        return refs[0].ref.net if refs and refs[0].ref else None

    @property
    def ref_set(self) -> frozenset[str]:
        """Reference nets of the run (every good side; partial sides when none is good)."""
        return frozenset(r.net for r in self.intervals[0].refs)

    @property
    def p0(self) -> Vec2:
        return self.intervals[0].p0

    @property
    def p1(self) -> Vec2:
        return self.intervals[-1].p1

    def midpoint(self) -> Vec2:
        half = self.length / 2
        acc = 0.0
        for i in self.intervals:
            if acc + i.length >= half:
                return seg_point(i.seg.seg, i.s0 + (half - acc))
            acc += i.length
        return self.p1

    @property
    def seg_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(i.seg.id for i in self.intervals))


def reference_layers(ctx: ErcContext) -> tuple[str, ...]:
    """Conductor layers that carry reference copper over a large part of the outline."""

    def build() -> tuple[str, ...]:
        geo = ctx.geometry()
        area = geo.outline().area or 1.0
        out = []
        for layer in geo.layer_ids():
            try:
                geo.conductor(layer)
            except Exception:
                continue
            tot = 0.0
            for net, mp in geo.copper_by_net(layer).items():
                if net is not None and ctx.net_kind(net) in ("ground", "power"):
                    tot += mp.area
            if tot / area >= MIN_PLANE_FRACTION:
                out.append(layer)
        return tuple(out)

    result: tuple[str, ...] = ctx.memo("ref_layers", build)
    return result


def _conductor_order(ctx: ErcContext) -> list[str]:
    return [lyr.id for lyr in ctx.board.stackup.layers if lyr.kind == "conductor"]


def adjacent_ref_layers(ctx: ErcContext, layer: str) -> tuple[str | None, str | None]:
    """(above, below) primary reference layers of ``layer``."""
    res: tuple[str | None, str | None] = ctx.memo(
        ("adjacent_ref_layers", layer), lambda: _adjacent_ref_layers(ctx, layer)
    )
    return res


def _adjacent_ref_layers(ctx: ErcContext, layer: str) -> tuple[str | None, str | None]:
    order = _conductor_order(ctx)
    refs = set(reference_layers(ctx))
    i = order.index(layer)
    above = next((order[k] for k in range(i - 1, -1, -1) if order[k] in refs), None)
    below = next((order[k] for k in range(i + 1, len(order)) if order[k] in refs), None)
    return above, below


def reference_map(ctx: ErcContext) -> ReferenceMap:
    rm: ReferenceMap = ctx.memo("refmap", lambda: ctx.geometry().reference_map())
    return rm


def _classify(
    ctx: ErcContext, layer: str, above: RefInfo | None, below: RefInfo | None, cov_ok: float
) -> tuple[str, RefInfo | None, tuple[RefInfo, ...]]:
    adj_a, adj_b = adjacent_ref_layers(ctx, layer)
    good: list[RefInfo] = []
    partial: list[RefInfo] = []
    for adj, ri in ((adj_a, above), (adj_b, below)):
        if adj is None or ri is None or ri.layer != adj:
            continue
        (good if ri.coverage >= cov_ok else partial).append(ri)
    if good:
        return "good", min(good, key=lambda r: r.distance), tuple(good)
    if partial:
        return "partial", min(partial, key=lambda r: r.distance), tuple(partial)
    return "void", None, ()


def net_runs(ctx: ErcContext, net: str, cov_ok: float = 0.8) -> list[Run]:
    """Runs of equal status along ``net`` with neighbour links inside each continuous chain."""

    def build() -> list[Run]:
        geo = ctx.geometry()
        rm = reference_map(ctx)
        chains: list[list[Run]] = []
        cur: list[Run] = []
        last_end: Vec2 | None = None
        last_layer: str | None = None
        for seg in geo.segments(net):
            for sp in rm.for_segment(seg.id):
                if sp.s_end - sp.s_start <= 1e-9:
                    continue
                st, ref, refs = _classify(ctx, seg.layer, sp.above, sp.below, cov_ok)
                p0 = seg_point(seg.seg, sp.s_start)
                p1 = seg_point(seg.seg, sp.s_end)
                contiguous = (
                    last_end is not None
                    and last_layer == seg.layer
                    and math.hypot(p0[0] - last_end[0], p0[1] - last_end[1]) < JOIN_TOL
                )
                if not contiguous and cur:
                    chains.append(cur)
                    cur = []
                iv = Interval(seg, sp.s_start, sp.s_end, st, ref, refs, p0, p1)
                if cur and cur[-1].status == st and cur[-1].intervals[0].refs_key == iv.refs_key:
                    cur[-1].intervals.append(iv)
                else:
                    cur.append(Run(net, seg.layer, st, [iv]))
                last_end, last_layer = p1, seg.layer
        if cur:
            chains.append(cur)
        for chain in chains:
            for a, b in itertools.pairwise(chain):
                a.next, b.prev = b, a
        return [r for chain in chains for r in chain]

    result: list[Run] = ctx.memo(("runs", net, cov_ok), build)
    return result


def run_line(run: Run) -> shapely.LineString:
    pts = [run.intervals[0].p0] + [i.p1 for i in run.intervals]
    if len(pts) < 2 or pts[0] == pts[-1]:
        pts = [pts[0], (pts[0][0] + 1e-9, pts[0][1])]
    return shapely.LineString(pts)
