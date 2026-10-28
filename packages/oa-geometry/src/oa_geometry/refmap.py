# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""ReferenceMap: the reference-plane (return-path) description of every routed segment.

Definitions (shared by ERC, EMI, SI and PI):

*Reference copper* of a layer is the copper of zones and graphic shapes, plus all copper of nets
whose kind is ``ground`` or ``power``. Copper of the segment's own net never references it.

For a segment piece and a layer ``C`` above/below it, with vertical distance ``h`` between the
trace surface and the facing plane surface, the *footprint* at position ``s`` is the cut line
perpendicular to the path of length ``W + 2*k*h`` (``k`` = ``k_buffer``). ``coverage`` is the
fraction of that line covered by the copper of one net; over a span it is the area-weighted
average over the span's footprint strip. ``edge_distance`` is the distance from the trace body
to the nearest boundary of that copper (0 when the trace crosses an edge), capped at ``5*h``.

Per side, the reference is the nearest layer whose best net covers at least ``MIN_COVERAGE`` of
the footprint; where none does, the best partial cover (> 0) is reported; otherwise ``None``.
Spans split wherever the reference layer, net or coverage changes.
"""

from __future__ import annotations

import itertools
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt
import shapely
from scipy.spatial import cKDTree
from shapely import STRtree
from shapely.ops import substring

from oa_geometry._shapes import as_multipolygon
from oa_geometry.models import (
    ReferenceMap,
    ReferenceSpan,
    RefInfo,
    ReturnVia,
    StitchCap,
    ViaTransition,
)
from oa_geometry.segs import Piece, reverse_piece
from oa_geometry.topology import all_pieces, net_pieces, net_topology

if TYPE_CHECKING:
    from oa_geometry.geometry import BoardGeometry

__all__ = [
    "MIN_COVERAGE",
    "piece_spans",
    "reference_map",
    "spans_for_net",
]

MIN_COVERAGE = 0.25
"""A layer is a usable reference when its best net covers at least this fraction."""
EDGE_FACTOR = 5.0
"""``edge_distance`` is capped at EDGE_FACTOR * h."""
RETURN_VIA_RADIUS = 5e-3
RETURN_VIA_COUNT = 4
STITCH_CAP_RADIUS = 10e-3
_EPS = 1e-9
_COV_MERGE = 0.1

Interval = tuple[float, float, str | None, float, float]  # a, b, net, coverage, edge_distance


@dataclass
class _RefLayer:
    layer: str
    parts: npt.NDArray[np.object_]
    nets: list[str]
    tree: STRtree
    edges: npt.NDArray[np.object_]
    edge_part: npt.NDArray[np.intp]
    edge_tree: STRtree
    shells: npt.NDArray[np.object_]
    holes: npt.NDArray[np.object_]
    hole_part: npt.NDArray[np.intp]
    hole_tree: STRtree | None
    net_names: list[str]
    part_net: npt.NDArray[np.intp]


def _ref_layer(geo: BoardGeometry, layer: str) -> _RefLayer | None:
    def make() -> _RefLayer | None:
        prims = geo._copper[layer].prims
        groups: dict[str, list[object]] = defaultdict(list)
        for g, n, ref in zip(prims.geoms, prims.nets, prims.refs, strict=True):
            if n is None:
                continue
            if ref.kind in ("zone", "shape") or geo.net_kind(n) in ("ground", "power"):
                groups[n].append(g)
        parts: list[object] = []
        nets: list[str] = []
        for n in sorted(groups):
            for p in as_multipolygon(shapely.union_all(groups[n])).geoms:
                parts.append(p)
                nets.append(n)
        if not parts:
            return None
        arr = np.empty(len(parts), dtype=object)
        arr[:] = parts
        seg_list: list[npt.NDArray[np.float64]] = []
        owner: list[npt.NDArray[np.intp]] = []
        for i, p in enumerate(parts):
            for ring in (p.exterior, *p.interiors):  # type: ignore[attr-defined]
                c = np.asarray(ring.coords)
                seg_list.append(np.stack((c[:-1], c[1:]), axis=1))
                owner.append(np.full(len(c) - 1, i, dtype=np.intp))
        segs = np.concatenate(seg_list)
        edges = shapely.linestrings(segs)
        shells = np.empty(len(parts), dtype=object)
        hole_list: list[object] = []
        hole_owner: list[int] = []
        for i, p in enumerate(parts):
            shells[i] = shapely.Polygon(p.exterior)  # type: ignore[attr-defined]
            for ring in p.interiors:  # type: ignore[attr-defined]
                hole_list.append(shapely.Polygon(ring))
                hole_owner.append(i)
        holes = np.empty(len(hole_list), dtype=object)
        holes[:] = hole_list
        return _RefLayer(
            layer=layer,
            parts=arr,
            nets=nets,
            tree=STRtree(arr),
            edges=edges,
            edge_part=np.concatenate(owner),
            edge_tree=STRtree(edges),
            shells=shells,
            holes=holes,
            hole_part=np.array(hole_owner, dtype=np.intp),
            hole_tree=STRtree(holes) if len(holes) else None,
            net_names=sorted(set(nets)),
            part_net=np.array([sorted(set(nets)).index(n) for n in nets], dtype=np.intp),
        )

    return geo._memoized(("reflayer", layer), make)  # type: ignore[no-any-return]


def _candidates(geo: BoardGeometry, layer: str, side: str) -> list[tuple[str, float]]:
    """Reference candidate layers (nearest first) with their vertical distances."""
    me = geo.conductor(layer)
    out: list[tuple[str, float]] = []
    for c in geo.idx.conductor_layers:
        if side == "above" and c.index < me.index:
            out.append((c.id, c.z_bottom - me.z_top))
        elif side == "below" and c.index > me.index:
            out.append((c.id, me.z_bottom - c.z_top))
    out.sort(key=lambda t: t[1])
    return [(lid, max(0.0, d)) for lid, d in out if _ref_layer(geo, lid) is not None]


# ---------------------------------------------------------------------------------------------
# Profiles of one candidate layer along a piece
# ---------------------------------------------------------------------------------------------


def _rects(
    p0: npt.NDArray[np.float64],
    d: npt.NDArray[np.float64],
    a: npt.NDArray[np.float64],
    b: npt.NDArray[np.float64],
    r: float,
) -> npt.NDArray[np.object_]:
    """Rectangles of half-width `r` along the straight path for each [a, b]."""
    n = np.array([-d[1], d[0]])
    pa = p0[None, :] + a[:, None] * d[None, :]
    pb = p0[None, :] + b[:, None] * d[None, :]
    ring = np.stack((pa + r * n, pb + r * n, pb - r * n, pa - r * n), axis=1)
    return shapely.polygons(ring)  # type: ignore[no-any-return]


def _part_edge_distance(
    rl: _RefLayer,
    parts: npt.NDArray[np.intp],
    bodies: npt.NDArray[np.object_],
    cap: float | npt.NDArray[np.float64],
) -> npt.NDArray[np.float64]:
    """Distance (capped) from body i to the boundary of part parts[i] (shell and holes)."""
    bb = shapely.bounds(bodies)
    x0, y0, x1, y1 = bb[:, 0] - cap, bb[:, 1] - cap, bb[:, 2] + cap, bb[:, 3] + cap
    shell = shapely.intersection(rl.shells[parts], shapely.box(x0, y0, x1, y1))
    out = np.minimum(shapely.distance(bodies, shapely.boundary(shell)), cap)
    out = np.where(np.isnan(out), cap, out)
    if rl.hole_tree is not None:
        hi_b, hi_h = rl.hole_tree.query(shapely.box(x0, y0, x1, y1))
        keep = rl.hole_part[hi_h] == parts[hi_b]
        hi_b, hi_h = hi_b[keep], hi_h[keep]
        if len(hi_b):
            np.minimum.at(out, hi_b, shapely.distance(bodies[hi_b], rl.holes[hi_h]))
    return np.asarray(out, dtype=np.float64)


def _profile(geo: BoardGeometry, piece: Piece, layer: str, h: float, k: float) -> list[Interval]:
    """Coverage profile of the candidate `layer` along `piece` (s along the polyline).

    Coverage of a strip is area(strip & shell) - sum(area(strip & hole)) per part: the holes of
    one polygon are disjoint, so no union of the (many) holes is ever built.
    """
    rl = _ref_layer(geo, layer)
    total = float(piece.line.length)
    cap = EDGE_FACTOR * max(h, 1e-6)
    if rl is None or total <= 0.0:
        return [(0.0, total, None, 0.0, cap)]
    w = piece.seg.width
    r_f = w / 2.0 + k * h
    pts = piece.pts
    straight = len(pts) == 2
    p0 = pts[0]
    d = (pts[-1] - pts[0]) / total
    if straight:
        foot = _rects(p0, d, np.array([0.0]), np.array([total]), r_f)[0]
    else:
        foot = shapely.buffer(piece.line, r_f, cap_style="flat", join_style="round")
    hit = [int(j) for j in rl.tree.query(foot) if rl.nets[int(j)] != piece.seg.net]
    if not hit:
        return [(0.0, total, None, 0.0, cap)]
    fx0, fy0, fx1, fy1 = foot.bounds
    win = (fx0 - cap, fy0 - cap, fx1 + cap, fy1 + cap)
    shells = {j: shapely.clip_by_rect(rl.shells[j], *win) for j in hit}
    hole_ix = np.array([], dtype=np.intp)
    if rl.hole_tree is not None:
        cand = rl.hole_tree.query(foot)
        hole_ix = cand[np.isin(rl.hole_part[cand], hit)]
    # breakpoints: vertices of shell & foot, and of the holes touching the footprint
    xy = np.vstack(
        [shapely.get_coordinates(shapely.intersection(foot, sc)) for sc in shells.values()]
        + [shapely.get_coordinates(rl.holes[hole_ix])]
    )
    bps = {0.0, total}
    if len(xy):
        if straight:
            proj = (xy - p0[None, :]) @ d
        else:
            proj = shapely.line_locate_point(piece.line, shapely.points(xy))
        bps.update(round(float(v), 12) for v in proj if _EPS < v < total - _EPS)
    merged = [0.0]
    for v in sorted(bps)[1:-1]:
        if v - merged[-1] > _EPS and total - v > _EPS:
            merged.append(v)
    merged.append(total)
    a = np.array(merged[:-1])
    b = np.array(merged[1:])
    if straight:
        strips = _rects(p0, d, a, b, r_f)
        bodies = _rects(p0, d, a, b, w / 2.0)
        area = (b - a) * 2.0 * r_f
    else:
        subs = [substring(piece.line, x, y) for x, y in zip(a, b, strict=True)]
        strips = shapely.buffer(subs, r_f, cap_style="flat", join_style="round")
        bodies = shapely.buffer(subs, w / 2.0, cap_style="flat", join_style="round")
        area = shapely.area(strips)
    n_nets = len(rl.net_names)
    cov = np.zeros((n_nets, len(a)))
    dist = np.full((n_nets, len(a)), cap)
    any_dist = np.full(len(a), cap)
    for j, sc in shells.items():
        net_ix = int(rl.part_net[j])
        cov[net_ix] += shapely.area(shapely.intersection(strips, sc))
        dj = _part_edge_distance(rl, np.full(len(a), j, dtype=np.intp), bodies, cap)
        dist[net_ix] = np.minimum(dist[net_ix], dj)
        any_dist = np.minimum(any_dist, dj)
    if len(hole_ix):
        si, hi = rl.hole_tree.query(strips)  # type: ignore[union-attr]
        keep = np.isin(hi, hole_ix)
        si, hi = si[keep], hi[keep]
        if len(si):
            inter = shapely.area(shapely.intersection(strips[si], rl.holes[hi]))
            np.subtract.at(cov, (rl.part_net[rl.hole_part[hi]], si), inter)
    cov = np.clip(cov / area[None, :], 0.0, 1.0)
    hit_nets = sorted({int(rl.part_net[j]) for j in hit})
    out: list[Interval] = []
    for i in range(len(a)):
        col = cov[:, i]
        n_best = int(np.argmax(col))
        c_best = float(col[n_best])
        if c_best > 1.0 - 1e-9:
            c_best = 1.0
        if c_best < 1e-9:
            net, c_best, e = None, 0.0, float(any_dist[i])
        else:
            net, e = rl.net_names[n_best], float(dist[n_best, i])
        out.append((float(a[i]), float(b[i]), net, c_best, min(e, cap)))
    del hit_nets
    return out


def _bulk_cover(
    geo: BoardGeometry, pieces: list[Piece], k: float
) -> dict[tuple[str, str], tuple[str, float, str, float]]:
    """Fast path: pieces whose footprint is entirely inside one part of the nearest reference
    layer. Returns {(piece id, side): (layer, h, net, edge_distance)}."""
    groups: dict[tuple[str, str, str], list[tuple[Piece, float]]] = defaultdict(list)
    for p in pieces:
        if p.line.length <= 0.0:
            continue
        for side in ("above", "below"):
            cands = _candidates(geo, p.seg.layer, side)
            if cands:
                groups[(p.seg.layer, side, cands[0][0])].append((p, cands[0][1]))
    out: dict[tuple[str, str], tuple[str, float, str, float]] = {}
    for (_, side, cand), members in groups.items():
        rl = _ref_layer(geo, cand)
        assert rl is not None
        lines = np.empty(len(members), dtype=object)
        lines[:] = [m[0].line for m in members]
        widths = np.array([m[0].seg.width for m in members])
        h = np.array([m[1] for m in members])
        foot = shapely.buffer(lines, widths / 2.0 + k * h, cap_style="flat", join_style="round")
        pi, pj = rl.tree.query(foot)
        if len(pi) == 0:
            continue
        own = np.array([m[0].seg.net for m in members], dtype=object)
        keep = np.array([rl.nets[j] != own[i] for i, j in zip(pi, pj, strict=True)], dtype=bool)
        pi, pj = pi[keep], pj[keep]
        if len(pi) == 0:
            continue
        shapely.prepare(rl.parts)
        cov = shapely.covers(rl.parts[pj], foot[pi])
        pi, pj = pi[cov], pj[cov]
        if len(pi) == 0:
            continue
        # one covering part per piece (first); edge distance to that part's boundary
        first = {}
        for i, j in zip(pi, pj, strict=True):
            first.setdefault(int(i), int(j))
        idx = np.array(sorted(first), dtype=np.intp)
        part_of = np.array([first[int(i)] for i in idx], dtype=np.intp)
        body = shapely.buffer(lines[idx], widths[idx] / 2.0, cap_style="flat", join_style="round")
        cap = EDGE_FACTOR * np.maximum(h[idx], 1e-6)
        edist = _part_edge_distance(rl, part_of, body, cap)
        for n, i in enumerate(idx):
            p = members[int(i)][0]
            out[(p.seg.id, side)] = (cand, float(h[i]), rl.nets[part_of[n]], float(edist[n]))
    return out


# ---------------------------------------------------------------------------------------------
# Combining candidates into per-side intervals, then spans
# ---------------------------------------------------------------------------------------------


def _at(profile: list[Interval], s: float) -> Interval:
    for iv in profile:
        if iv[0] - _EPS <= s <= iv[1] + _EPS:
            return iv
    return profile[-1]


def _side_intervals(
    geo: BoardGeometry,
    piece: Piece,
    side: str,
    k: float,
    fast: dict[tuple[str, str], tuple[str, float, str, float]],
) -> list[tuple[float, float, RefInfo | None]]:
    total = float(piece.line.length)
    cands = _candidates(geo, piece.seg.layer, side)
    if not cands:
        return [(0.0, total, None)]
    done: list[tuple[str, float, list[Interval]]] = []
    for ci, (lid, h) in enumerate(cands):
        hit = fast.get((piece.seg.id, side)) if ci == 0 else None
        if hit is not None and hit[0] == lid:
            prof: list[Interval] = [(0.0, total, hit[2], 1.0, hit[3])]
        else:
            prof = _profile(geo, piece, lid, h, k)
        done.append((lid, h, prof))
        if all(iv[3] >= MIN_COVERAGE for iv in prof):
            break
    bps = sorted({v for _, _, prof in done for iv in prof for v in (iv[0], iv[1])})
    out: list[tuple[float, float, RefInfo | None]] = []
    for a, b in itertools.pairwise(bps):
        if b - a <= _EPS:
            continue
        mid = (a + b) / 2.0
        chosen: tuple[str, float, Interval] | None = None
        weak: tuple[str, float, Interval] | None = None
        for lid, h, prof in done:
            iv = _at(prof, mid)
            if iv[2] is None:
                continue
            if iv[3] >= MIN_COVERAGE:
                chosen = (lid, h, iv)
                break
            if weak is None or iv[3] > weak[2][3]:
                weak = (lid, h, iv)
        pick = chosen or weak
        info = (
            None
            if pick is None or pick[2][2] is None
            else RefInfo(
                layer=pick[0],
                net=pick[2][2],
                distance=pick[1],
                coverage=min(1.0, pick[2][3]),
                edge_distance=pick[2][4],
            )
        )
        out.append((a, b, info))
    return out


def _close(x: RefInfo | None, y: RefInfo | None) -> bool:
    """Whether two neighbouring references can share a span."""
    if x is None or y is None:
        return x is None and y is None
    return (
        x.layer == y.layer
        and x.net == y.net
        and abs(x.coverage - y.coverage) <= _COV_MERGE
        and abs(x.edge_distance - y.edge_distance) <= 0.5 * max(x.distance, 1e-6)
    )


def _blend(x: RefInfo | None, wx: float, y: RefInfo | None, wy: float) -> RefInfo | None:
    """Length-weighted coverage, minimum edge distance."""
    if x is None or y is None:
        return x
    cov = (x.coverage * wx + y.coverage * wy) / (wx + wy)
    return x.model_copy(
        update={"coverage": min(1.0, cov), "edge_distance": min(x.edge_distance, y.edge_distance)}
    )


def _compute_spans(
    geo: BoardGeometry,
    piece: Piece,
    k: float,
    fast: dict[tuple[str, str], tuple[str, float, str, float]],
) -> tuple[ReferenceSpan, ...]:
    total = float(piece.line.length)
    if total <= 0.0:
        return ()
    scale = piece.seg.length / total
    above = _side_intervals(geo, piece, "above", k, fast)
    below = _side_intervals(geo, piece, "below", k, fast)
    bps = sorted({v for lst in (above, below) for iv in lst for v in (iv[0], iv[1])})
    rows: list[tuple[float, float, RefInfo | None, RefInfo | None]] = []

    def pick(lst: list[tuple[float, float, RefInfo | None]], s: float) -> RefInfo | None:
        for a, b, r in lst:
            if a - _EPS <= s <= b + _EPS:
                return r
        return None

    for a, b in itertools.pairwise(bps):
        if b - a <= _EPS:
            continue
        mid = (a + b) / 2.0
        ra, rb = pick(above, mid), pick(below, mid)
        if rows and _close(rows[-1][2], ra) and _close(rows[-1][3], rb):
            a0, b0, ca, cb = rows[-1]
            rows[-1] = (a0, b, _blend(ca, b0 - a0, ra, b - a), _blend(cb, b0 - a0, rb, b - a))
        else:
            rows.append((a, b, ra, rb))
    if not rows:
        rows = [(0.0, total, None, None)]
    return tuple(
        ReferenceSpan(s_start=a * scale, s_end=min(b * scale, piece.seg.length), above=ra, below=rb)
        for a, b, ra, rb in rows
    )


def _span_cache(geo: BoardGeometry, k: float) -> dict[str, tuple[ReferenceSpan, ...]]:
    return geo._memoized(("spans", k), dict)  # type: ignore[no-any-return]


def _piece_index(geo: BoardGeometry) -> dict[str, Piece]:
    return geo._memoized(  # type: ignore[no-any-return]
        "piece_index", lambda: {p.seg.id: p for p in all_pieces(geo)}
    )


def piece_spans(geo: BoardGeometry, seg_id: str, k: float = 3.0) -> tuple[ReferenceSpan, ...]:
    """Reference spans of one piece (computed on demand and cached)."""
    cache = _span_cache(geo, k)
    if seg_id not in cache:
        piece = _piece_index(geo)[seg_id]
        cache[seg_id] = _compute_spans(geo, piece, k, _bulk_cover(geo, [piece], k))
    return cache[seg_id]


def spans_for_net(geo: BoardGeometry, net: str, k: float = 3.0) -> None:
    """Warm the cache for all pieces of `net`."""
    pcs = [p for p in net_pieces(geo, net) if p.seg.id not in _span_cache(geo, k)]
    if not pcs:
        return
    fast = _bulk_cover(geo, pcs, k)
    cache = _span_cache(geo, k)
    for p in pcs:
        cache[p.seg.id] = _compute_spans(geo, p, k, fast)


def flip_spans(spans: tuple[ReferenceSpan, ...], length: float) -> tuple[ReferenceSpan, ...]:
    """Spans of the reversed segment."""
    return tuple(
        ReferenceSpan(
            s_start=max(0.0, length - sp.s_end),
            s_end=max(0.0, length - sp.s_start),
            above=sp.above,
            below=sp.below,
        )
        for sp in reversed(spans)
    )


# ---------------------------------------------------------------------------------------------
# Whole-board map and via transitions
# ---------------------------------------------------------------------------------------------


def reference_map(geo: BoardGeometry, k: float = 3.0) -> ReferenceMap:
    """The ReferenceMap of all non-ground routed segments."""

    def make() -> ReferenceMap:
        pcs = [p for p in all_pieces(geo) if geo.net_kind(p.seg.net) != "ground"]
        cache = _span_cache(geo, k)
        todo = [p for p in pcs if p.seg.id not in cache]
        fast = _bulk_cover(geo, todo, k)
        for p in todo:
            cache[p.seg.id] = _compute_spans(geo, p, k, fast)
        spans = tuple((p.seg.id, cache[p.seg.id]) for p in pcs)
        return ReferenceMap(k_buffer=k, spans=spans, via_transitions=_via_transitions(geo, k))

    return geo._memoized(("refmap", k), make)  # type: ignore[no-any-return]


def _primary_ref(span: ReferenceSpan) -> RefInfo | None:
    cands = [r for r in (span.above, span.below) if r is not None]
    return min(cands, key=lambda r: r.distance) if cands else None


def _via_transitions(geo: BoardGeometry, k: float) -> tuple[ViaTransition, ...]:
    board = geo.board
    sig_nets = sorted(
        {
            v.net
            for v in board.vias
            if v.net is not None and geo.net_kind(v.net) not in ("ground", "power")
        }
    )
    # lookup structures for return vias and caps
    ref_via_xy: dict[str, tuple[cKDTree | None, list[str]]] = {}
    by_net: dict[str, list[tuple[float, float, str]]] = defaultdict(list)
    for v in board.vias:
        if v.net is not None:
            by_net[v.net].append((v.position[0], v.position[1], v.id))
    caps = _capacitors(geo)
    out: list[ViaTransition] = []
    for net in sig_nets:
        spans_for_net(geo, net, k)
        topo = net_topology(geo, net)
        pieces = {p.seg.id: p for p in net_pieces(geo, net)}
        cache = _span_cache(geo, k)
        inc: dict[str, list[str]] = defaultdict(list)
        for e in topo.edges:
            if e.kind == "segment" and e.seg_id:
                inc[e.a].append(e.seg_id)
                inc[e.b].append(e.seg_id)
        nodes = {n.id: n for n in topo.nodes}
        per_via: dict[str, list[tuple[int, str, RefInfo | None]]] = defaultdict(list)
        for n in topo.nodes:
            if n.kind != "via" or n.ref is None or n.layer is None or not inc.get(n.id):
                continue
            sid = inc[n.id][0]
            pc = pieces[sid]
            spans = cache.get(sid, ())
            if not spans:
                continue
            at_start = math.hypot(
                pc.start[0] - n.position[0], pc.start[1] - n.position[1]
            ) <= math.hypot(pc.end[0] - n.position[0], pc.end[1] - n.position[1])
            ref = _primary_ref(spans[0] if at_start else spans[-1])
            per_via[n.ref].append((geo.conductor(n.layer).index, n.layer, ref))
        del nodes
        for vid in sorted(per_via):
            ends = sorted(per_via[vid])
            if len(ends) < 2:
                continue
            (_, l0, r0), (_, l1, r1) = ends[0], ends[-1]
            via = geo.idx.via(vid)
            ref_nets = sorted({r.net for r in (r0, r1) if r is not None})
            rvias: list[ReturnVia] = []
            for rn in ref_nets:
                if rn not in ref_via_xy:
                    pts = by_net.get(rn, [])
                    ref_via_xy[rn] = (
                        cKDTree(np.array([[p[0], p[1]] for p in pts])) if pts else None,
                        [p[2] for p in pts],
                    )
                tree, ids = ref_via_xy[rn]
                if tree is None:
                    continue
                dd, ii = tree.query(
                    via.position,
                    k=min(RETURN_VIA_COUNT, len(ids)),
                    distance_upper_bound=RETURN_VIA_RADIUS,
                )
                for d, i in zip(np.atleast_1d(dd), np.atleast_1d(ii), strict=True):
                    if math.isfinite(d):
                        rvias.append(ReturnVia(via=ids[int(i)], net=rn, distance=float(d)))
            rvias.sort(key=lambda r: (r.distance, r.via))
            scaps = [
                StitchCap(refdes=rd, distance=d, nets=pair)
                for rd, pos, pair in caps
                if (set(pair) & set(ref_nets))
                and (d := math.hypot(pos[0] - via.position[0], pos[1] - via.position[1]))
                <= STITCH_CAP_RADIUS
            ]
            scaps.sort(key=lambda c: (c.distance, c.refdes))
            out.append(
                ViaTransition(
                    via=vid,
                    net=net,
                    position=via.position,
                    from_layer=l0,
                    to_layer=l1,
                    from_ref=r0,
                    to_ref=r1,
                    ref_net_changes=(r0 is not None and r1 is not None and r0.net != r1.net),
                    return_vias=tuple(rvias[:RETURN_VIA_COUNT]),
                    stitching_caps=tuple(scaps),
                )
            )
    return tuple(out)


def _capacitors(geo: BoardGeometry) -> list[tuple[str, tuple[float, float], tuple[str, str]]]:
    """Two-pad components named C* whose pads sit on two distinct ground/power nets."""
    out = []
    for c in geo.board.components:
        if not c.refdes.upper().startswith("C") or len(c.pads) != 2:
            continue
        nets = []
        for pid in c.pads:
            try:
                nets.append(geo.idx.pad(pid).net)
            except KeyError:
                break
        if len(nets) != 2 or None in nets or nets[0] == nets[1]:
            continue
        a, b = nets
        assert a is not None and b is not None
        if all(geo.net_kind(n) in ("ground", "power") for n in (a, b)):
            out.append((c.refdes, c.position, (a, b)))
    return out


__all__ += ["flip_spans", "reverse_piece"]
