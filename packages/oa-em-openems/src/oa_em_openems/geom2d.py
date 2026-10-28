# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small 2D polygon helpers: keyhole polygons (CSXCAD polygons have no holes) and edge normals."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

Pt = tuple[float, float]


def _cross(o: Pt, a: Pt, b: Pt) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _segments_properly_cross(p1: Pt, p2: Pt, q1: Pt, q2: Pt, eps: float) -> bool:
    """True when the open segments p1p2 and q1q2 cross (touching at endpoints does not count)."""
    d1 = _cross(p1, p2, q1)
    d2 = _cross(p1, p2, q2)
    d3 = _cross(q1, q2, p1)
    d4 = _cross(q1, q2, p2)
    scale = eps * eps
    if abs(d1) < scale or abs(d2) < scale or abs(d3) < scale or abs(d4) < scale:
        return False
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)


def point_in_ring(pt: Pt, ring: Sequence[Pt]) -> bool:
    """Even-odd point-in-polygon test."""
    x, y = pt
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def keyhole_ring(exterior: Sequence[Pt], holes: Sequence[Sequence[Pt]]) -> list[Pt]:
    """Merge ``holes`` into ``exterior`` with zero-width bridges (a "keyhole" polygon).

    The result is one ring that an even-odd rasterizer fills like the polygon with holes. A bridge
    joins the closest pair of vertices whose connecting segment crosses no ring edge and lies in the
    material. Holes are bridged in order of decreasing maximum x so that the result is
    deterministic.
    """
    if not holes:
        return list(exterior)
    scale = max(
        (max(abs(p[0]), abs(p[1])) for ring in (exterior, *holes) for p in ring), default=1.0
    )
    eps = 1e-9 * max(scale, 1e-12)
    outer = list(exterior)
    remaining = sorted(
        (list(h) for h in holes), key=lambda h: (-max(p[0] for p in h), -max(p[1] for p in h))
    )
    for k, hole in enumerate(remaining):
        others = remaining[k + 1 :]
        edges: list[tuple[Pt, Pt]] = []
        for ring in (outer, hole, *others):
            n = len(ring)
            edges.extend((ring[i], ring[(i + 1) % n]) for i in range(n))
        best: tuple[float, int, int] | None = None
        for hi, hp in enumerate(hole):
            for oi, op in enumerate(outer):
                dist = math.hypot(hp[0] - op[0], hp[1] - op[1])
                if best is not None and dist >= best[0]:
                    continue
                if any(_segments_properly_cross(hp, op, a, b, eps) for a, b in edges):
                    continue
                mid = ((hp[0] + op[0]) / 2.0, (hp[1] + op[1]) / 2.0)
                if not point_in_ring(mid, exterior):
                    continue
                if any(point_in_ring(mid, h) for h in remaining):
                    continue
                best = (dist, hi, oi)
        if best is None:
            raise ValueError("cannot bridge hole to the polygon exterior")
        _, hi, oi = best
        rot = hole[hi:] + hole[:hi]
        outer = [*outer[: oi + 1], *rot, rot[0], outer[oi], *outer[oi + 1 :]]
    return outer


def ring_edge_normals(ring: np.ndarray) -> list[tuple[Pt, Pt, Pt]]:
    """Edges of a ring as ``(a, b, outward_normal)``; outward is away from the material.

    Exteriors are counter-clockwise and holes clockwise, so material is always on the left of an
    edge and the outward normal on its right.
    """
    out: list[tuple[Pt, Pt, Pt]] = []
    n = len(ring)
    for i in range(n):
        a = (float(ring[i][0]), float(ring[i][1]))
        b = (float(ring[(i + 1) % n][0]), float(ring[(i + 1) % n][1]))
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length == 0.0:
            continue
        out.append((a, b, (dy / length, -dx / length)))
    return out
