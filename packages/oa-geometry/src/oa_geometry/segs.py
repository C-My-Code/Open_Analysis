# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Routed-segment pieces: tracks split into single lines/arcs, cut at T-junctions."""

from __future__ import annotations

import itertools
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt
import shapely
from shapely import LineString, STRtree

from oa_geometry._shapes import arc_points, seg_length
from oa_geometry.models import RoutedSegment
from oa_model.geometry import ArcSeg, LineSeg, PathSeg

if TYPE_CHECKING:
    from oa_geometry.geometry import BoardGeometry

F64 = npt.NDArray[np.float64]
JOIN_EPS = 1e-9
"""Endpoint-to-body tolerance for deciding that two track ends meet [m] (the 1 nm grid)."""


@dataclass(frozen=True)
class Piece:
    """A RoutedSegment plus its tessellated polyline (exact for lines)."""

    seg: RoutedSegment
    pts: F64
    line: LineString
    track: str

    @property
    def start(self) -> tuple[float, float]:
        return (float(self.pts[0, 0]), float(self.pts[0, 1]))

    @property
    def end(self) -> tuple[float, float]:
        return (float(self.pts[-1, 0]), float(self.pts[-1, 1]))


def reverse_seg(seg: PathSeg) -> PathSeg:
    """The same segment travelled the other way."""
    if isinstance(seg, LineSeg):
        return LineSeg(start=seg.end, end=seg.start)
    return ArcSeg(start=seg.end, end=seg.start, center=seg.center, ccw=not seg.ccw)


def reverse_piece(p: Piece) -> Piece:
    return Piece(
        seg=p.seg.model_copy(update={"seg": reverse_seg(p.seg.seg)}),
        pts=p.pts[::-1].copy(),
        line=shapely.reverse(p.line),
        track=p.track,
    )


def _split_seg(seg: PathSeg, fractions: list[float]) -> list[PathSeg]:
    """Split a segment at increasing length fractions in (0, 1)."""
    if isinstance(seg, LineSeg):
        pts = [seg.start]
        for f in fractions:
            pts.append(
                (
                    seg.start[0] + f * (seg.end[0] - seg.start[0]),
                    seg.start[1] + f * (seg.end[1] - seg.start[1]),
                )
            )
        pts.append(seg.end)
        return [LineSeg(start=a, end=b) for a, b in itertools.pairwise(pts)]
    cx, cy = seg.center
    r = math.hypot(seg.start[0] - cx, seg.start[1] - cy)
    a0 = math.atan2(seg.start[1] - cy, seg.start[0] - cx)
    from oa_geometry._shapes import arc_sweep

    sweep = arc_sweep(seg)
    pts = [seg.start]
    for f in fractions:
        a = a0 + f * sweep
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    pts.append(seg.end)
    return [
        ArcSeg(start=a, end=b, center=seg.center, ccw=seg.ccw) for a, b in itertools.pairwise(pts)
    ]


def _make_piece(
    seg: PathSeg, id_: str, net: str, layer: str, width: float, track: str, tol: float
) -> Piece:
    pts = (
        np.array([seg.start, seg.end], dtype=np.float64)
        if isinstance(seg, LineSeg)
        else (arc_points(seg, tol))
    )
    rs = RoutedSegment(
        id=id_, net=net, layer=layer, width=width, seg=seg, length=seg_length(seg), s_start=0.0
    )
    return Piece(seg=rs, pts=pts, line=shapely.linestrings(pts), track=track)


def build_pieces(geo: BoardGeometry) -> tuple[Piece, ...]:
    """All routed pieces of all nets in board order, cut at T-junctions of the same net."""
    board, tol = geo.board, geo.arc_tol
    raw: list[Piece] = []
    for t in board.tracks:
        if t.net is None or t.layer not in geo._copper:
            continue
        multi = len(t.path) > 1
        for k, seg in enumerate(t.path):
            raw.append(
                _make_piece(
                    seg, f"{t.id}#{k}" if multi else t.id, t.net, t.layer, t.width, t.id, tol
                )
            )
    cuts = _tjunction_cuts(raw)
    if not cuts:
        return tuple(raw)
    out: list[Piece] = []
    for i, p in enumerate(raw):
        fr = cuts.get(i)
        if not fr:
            out.append(p)
            continue
        for j, sub in enumerate(_split_seg(p.seg.seg, fr)):
            out.append(
                _make_piece(
                    sub, f"{p.seg.id}.{j}", p.seg.net, p.seg.layer, p.seg.width, p.track, tol
                )
            )
    return tuple(out)


def _tjunction_cuts(pieces: list[Piece]) -> dict[int, list[float]]:
    """Length fractions at which same-net, same-layer track ends land inside another piece."""
    by_layer: dict[str, list[int]] = defaultdict(list)
    for i, p in enumerate(pieces):
        by_layer[p.seg.layer].append(i)
    cuts: dict[int, set[float]] = defaultdict(set)
    for ids in by_layer.values():
        if len(ids) < 2:
            continue
        lines = np.empty(len(ids), dtype=object)
        lines[:] = [pieces[i].line for i in ids]
        pts = np.array(
            [pt for i in ids for pt in (pieces[i].start, pieces[i].end)], dtype=np.float64
        )
        owner = np.repeat(np.arange(len(ids)), 2)
        halfw = max(pieces[i].seg.width for i in ids) / 2.0
        tree = STRtree(lines)
        pi, li = tree.query(shapely.points(pts), predicate="dwithin", distance=halfw)
        for a, b in zip(pi, li, strict=True):
            if owner[a] == b:
                continue
            pa, pb = pieces[ids[owner[a]]], pieces[ids[b]]
            if pa.seg.net != pb.seg.net:
                continue
            d = float(shapely.distance(shapely.points(pts[a]), pb.line))
            if d > pb.seg.width / 2.0 + JOIN_EPS:
                continue
            total = float(pb.line.length)
            if total <= 0.0:
                continue
            at = float(shapely.line_locate_point(pb.line, shapely.points(pts[a])))
            if JOIN_EPS < at < total - JOIN_EPS:
                cuts[ids[b]].add(round(at / total, 12))
    return {k: sorted(v) for k, v in cuts.items()}
