# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Chain ``Edge.Cuts`` items into closed loops and build :class:`oa_model.BoardOutline`."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from oa_model import BoardOutline, Diagnostic, Points, Polygon
from oa_model.geometry import orient_ring, signed_area
from shapely.geometry import Polygon as ShPolygon

from ._geom import PathSegT, from_shapely, reverse_seg, seg_points

CHAIN_TOL = 1e-6  # metres: KiCad's default outline chaining tolerance (1 um)


@dataclass
class Piece:
    closed: bool
    segs: list[PathSegT]


@dataclass
class Loop:
    segs: list[PathSegT]
    ring: np.ndarray  # discretized, CCW, not closed
    area: float


def _cell(p: tuple[float, float]) -> tuple[int, int]:
    return (round(p[0] / CHAIN_TOL), round(p[1] / CHAIN_TOL))


def _near(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= CHAIN_TOL


def chain_pieces(
    pieces: list[Piece], diags: list[Diagnostic]
) -> list[list[PathSegT]]:
    """Closed loops from pieces (greedy, tolerance :data:`CHAIN_TOL`); open chains are reported."""
    loops: list[list[PathSegT]] = [p.segs for p in pieces if p.closed]
    open_pieces = [p.segs for p in pieces if not p.closed]
    n = len(open_pieces)
    used = [False] * n
    grid: dict[tuple[int, int], list[tuple[int, bool]]] = {}  # cell -> (piece, at_start)
    for i, segs in enumerate(open_pieces):
        for at_start, pt in ((True, segs[0].start), (False, segs[-1].end)):
            grid.setdefault(_cell(pt), []).append((i, at_start))

    def find(pt: tuple[float, float]) -> tuple[int, bool] | None:
        cx, cy = _cell(pt)
        best: tuple[float, int, bool] | None = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i, at_start in grid.get((cx + dx, cy + dy), ()):
                    if used[i]:
                        continue
                    q = open_pieces[i][0].start if at_start else open_pieces[i][-1].end
                    d = math.hypot(q[0] - pt[0], q[1] - pt[1])
                    if d <= CHAIN_TOL and (best is None or d < best[0]):
                        best = (d, i, at_start)
        return None if best is None else (best[1], best[2])

    for i in range(n):
        if used[i]:
            continue
        used[i] = True
        chain = list(open_pieces[i])
        while not _is_closed(chain):
            nxt = find(chain[-1].end)
            if nxt is None:
                break
            j, at_start = nxt
            used[j] = True
            segs = open_pieces[j] if at_start else [reverse_seg(s) for s in reversed(open_pieces[j])]
            chain.extend(segs)
        if _is_closed(chain):
            loops.append(chain)
        else:
            p = chain[0].start
            diags.append(
                Diagnostic(
                    severity="warning",
                    code="INGEST.OUTLINE_OPEN",
                    message=(
                        f"Edge.Cuts chain of {len(chain)} segment(s) is not closed within "
                        f"{CHAIN_TOL * 1e6:g} um"
                    ),
                    location=(float(p[0]), float(p[1])),
                    path="/outline",
                    hint="close the board outline in KiCad (check the gap near this location)",
                )
            )
    return loops


def _is_closed(chain: list[PathSegT]) -> bool:
    return _near(chain[-1].end, chain[0].start) and (
        len(chain) > 1 or _seg_len(chain[0]) > CHAIN_TOL
    )


def _seg_len(s: PathSegT) -> float:
    return math.hypot(s.end[0] - s.start[0], s.end[1] - s.start[1])


def _loop_ring(segs: list[PathSegT], tol: float) -> np.ndarray:
    parts = []
    for s in segs:
        p = seg_points(s, tol)
        parts.append(p[:-1])
    return np.vstack(parts)


def build_outline(
    pieces: list[Piece], tol: float, diags: list[Diagnostic]
) -> BoardOutline | None:
    """Outline from Edge.Cuts pieces; ``None`` if there is no usable closed loop."""
    raw = chain_pieces(pieces, diags)
    loops: list[Loop] = []
    for segs in raw:
        ring = _loop_ring(segs, tol)
        if ring.shape[0] < 3:
            continue
        a = signed_area(ring)
        if abs(a) < 1e-18:
            continue
        if a < 0:  # make CCW; reverse segments to keep them consistent with the ring
            segs = [reverse_seg(s) for s in reversed(segs)]
            ring = ring[::-1]
            a = -a
        loops.append(Loop(segs, ring, a))
    if not loops:
        return None
    sh = [ShPolygon(lp.ring) for lp in loops]
    order = sorted(range(len(loops)), key=lambda i: -loops[i].area)
    parent: dict[int, int | None] = {}
    depth: dict[int, int] = {}
    for rank, i in enumerate(order):
        par = None
        for j in reversed(order[:rank]):  # smallest containing loop first
            if sh[j].contains(sh[i].representative_point()):
                par = j
                break
        parent[i] = par
        depth[i] = 0 if par is None else depth[par] + 1
    exteriors = [i for i in order if depth[i] % 2 == 0]
    main = exteriors[0]

    def holes_of(ext: int) -> tuple[Points, ...]:
        hs = [i for i in order if depth[i] % 2 == 1 and parent[i] == ext]
        return tuple(Points(loops[i].ring[::-1]) for i in hs)

    def poly(ext: int) -> Polygon:
        return Polygon(exterior=Points(loops[ext].ring), holes=holes_of(ext)).normalized()

    return BoardOutline(
        polygon=poly(main),
        edges=tuple(loops[main].segs),
        extra_boards=tuple(poly(i) for i in exteriors[1:]),
    )


def bbox_outline(points: np.ndarray, margin: float = 1e-3) -> BoardOutline:
    """Fallback outline: bounding box of the copper, used only when Edge.Cuts is unusable."""
    if points.shape[0] == 0:
        x0 = y0 = 0.0
        x1 = y1 = 1e-3
    else:
        x0, y0 = points.min(axis=0) - margin
        x1, y1 = points.max(axis=0) + margin
    ring = Points([x0, y0, x1, y0, x1, y1, x0, y1])
    return BoardOutline(polygon=Polygon(exterior=orient_ring(ring, True)))


__all__ = ["CHAIN_TOL", "Piece", "bbox_outline", "build_outline", "chain_pieces", "from_shapely"]
