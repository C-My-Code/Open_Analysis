# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Geometry helpers: 3-point arcs, discretization and shapely <-> oa_model polygon conversion.

Everything here works in the model frame (metres, Y up) unless a function says otherwise.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import numpy.typing as npt
import shapely
from shapely.geometry import LinearRing, MultiPolygon
from shapely.geometry import Polygon as ShPolygon
from shapely.geometry.base import BaseGeometry

from oa_model import ArcSeg, LineSeg, Points, Polygon
from oa_model.geometry import signed_area

Arr = npt.NDArray[np.float64]
Pt = tuple[float, float]
PathSegT = LineSeg | ArcSeg

MM = 1e-3
TWO_PI = 2.0 * math.pi


class Arc:
    """Circular arc through three points (model frame)."""

    __slots__ = ("ccw", "center", "end", "radius", "start")

    def __init__(self, start: Pt, center: Pt, end: Pt, ccw: bool) -> None:
        self.start = start
        self.center = center
        self.end = end
        self.ccw = ccw
        self.radius = math.hypot(start[0] - center[0], start[1] - center[1])

    def sweep(self) -> float:
        """Signed sweep angle in radians (positive = counter-clockwise), in (-2pi, 2pi]."""
        a0 = math.atan2(self.start[1] - self.center[1], self.start[0] - self.center[0])
        a1 = math.atan2(self.end[1] - self.center[1], self.end[0] - self.center[0])
        if self.ccw:
            d = (a1 - a0) % TWO_PI
            return d if d > 0 else TWO_PI
        d = (a0 - a1) % TWO_PI
        return -d if d > 0 else -TWO_PI


def arc_from_three(s: Pt, m: Pt, e: Pt) -> Arc | None:
    """Circle through three points; ``None`` if (nearly) collinear.

    The circumcentre is computed relative to ``s`` for conditioning. ``ccw`` is the direction of
    travel s -> m -> e.
    """
    bx, by = m[0] - s[0], m[1] - s[1]
    cx, cy = e[0] - s[0], e[1] - s[1]
    d = 2.0 * (bx * cy - by * cx)
    scale = max(abs(bx), abs(by), abs(cx), abs(cy))
    if scale == 0.0 or abs(d) <= 1e-12 * scale * scale:
        return None
    b2 = bx * bx + by * by
    c2 = cx * cx + cy * cy
    ux = (cy * b2 - by * c2) / d
    uy = (bx * c2 - cx * b2) / d
    center = (s[0] + ux, s[1] + uy)
    ccw = (bx * (e[1] - m[1]) - by * (e[0] - m[0])) > 0.0
    return Arc(s, center, e, ccw)


def arc_steps(radius: float, sweep: float, tol: float) -> int:
    """Number of chords so the sagitta error stays below ``tol``."""
    if radius <= tol:
        return max(4, math.ceil(abs(sweep) / (math.pi / 2)))
    step = 2.0 * math.acos(1.0 - tol / radius)
    return max(2, math.ceil(abs(sweep) / step))


def arc_points(arc: Arc, tol: float) -> Arr:
    """Points along the arc including both endpoints exactly."""
    sweep = arc.sweep()
    n = arc_steps(arc.radius, sweep, tol)
    a0 = math.atan2(arc.start[1] - arc.center[1], arc.start[0] - arc.center[0])
    t = a0 + sweep * np.arange(n + 1) / n
    pts = np.empty((n + 1, 2), dtype=np.float64)
    pts[:, 0] = arc.center[0] + arc.radius * np.cos(t)
    pts[:, 1] = arc.center[1] + arc.radius * np.sin(t)
    pts[0] = arc.start
    pts[-1] = arc.end
    return pts


def circle_points(center: Pt, radius: float, tol: float) -> Arr:
    n = max(8, arc_steps(radius, TWO_PI, tol))
    t = TWO_PI * np.arange(n) / n
    return np.column_stack((center[0] + radius * np.cos(t), center[1] + radius * np.sin(t)))


def bezier_points(p: Sequence[Pt], tol: float) -> Arr:
    """Flatten a cubic Bezier by recursive subdivision (flatness below ``tol``)."""
    out: list[Pt] = [p[0]]

    def flat(a: Pt, b: Pt, c: Pt, d: Pt) -> bool:
        # distance of the control points from the chord a-d
        dx, dy = d[0] - a[0], d[1] - a[1]
        ln = math.hypot(dx, dy)
        if ln == 0.0:
            return (
                max(math.hypot(b[0] - a[0], b[1] - a[1]), math.hypot(c[0] - a[0], c[1] - a[1]))
                <= tol
            )
        d1 = abs((b[0] - a[0]) * dy - (b[1] - a[1]) * dx) / ln
        d2 = abs((c[0] - a[0]) * dy - (c[1] - a[1]) * dx) / ln
        return max(d1, d2) <= tol

    def rec(a: Pt, b: Pt, c: Pt, d: Pt, depth: int) -> None:
        if depth > 18 or flat(a, b, c, d):
            out.append(d)
            return
        ab = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        bc = ((b[0] + c[0]) / 2, (b[1] + c[1]) / 2)
        cd = ((c[0] + d[0]) / 2, (c[1] + d[1]) / 2)
        abc = ((ab[0] + bc[0]) / 2, (ab[1] + bc[1]) / 2)
        bcd = ((bc[0] + cd[0]) / 2, (bc[1] + cd[1]) / 2)
        m = ((abc[0] + bcd[0]) / 2, (abc[1] + bcd[1]) / 2)
        rec(a, ab, abc, m, depth + 1)
        rec(m, bcd, cd, d, depth + 1)

    rec(p[0], p[1], p[2], p[3], 0)
    return np.array(out, dtype=np.float64)


# ----------------------------------------------------------------------------------------
# rings and polygons
# ----------------------------------------------------------------------------------------
def clean_ring(a: Arr, eps: float = 1e-12) -> Arr:
    """Drop the closing duplicate and consecutive duplicate points."""
    if a.shape[0] == 0:
        return a
    keep = np.ones(a.shape[0], dtype=bool)
    d = np.abs(np.diff(a, axis=0)).max(axis=1) if a.shape[0] > 1 else np.zeros(0)
    keep[1:] = d > eps
    a = a[keep]
    if a.shape[0] > 1 and np.abs(a[0] - a[-1]).max() <= eps:
        a = a[:-1]
    return a


def to_shapely(ring: Arr, holes: Iterable[Arr] = ()) -> ShPolygon:
    return ShPolygon(ring, [h for h in holes if len(h) >= 3])


def from_shapely(geom: object, *, min_area: float = 0.0) -> tuple[Polygon, ...]:
    """Convert a shapely (Multi)Polygon / collection to normalized ``oa_model`` polygons."""
    polys: list[ShPolygon] = []
    _collect_polygons(geom, polys)
    out: list[Polygon] = []
    for p in polys:
        if p.is_empty or p.area <= min_area:
            continue
        p = shapely.geometry.polygon.orient(p, 1.0)
        ext = clean_ring(np.asarray(p.exterior.coords, dtype=np.float64))
        if ext.shape[0] < 3:
            continue
        holes = []
        for r in p.interiors:
            h = clean_ring(np.asarray(r.coords, dtype=np.float64))
            if h.shape[0] >= 3:
                holes.append(Points(h))
        out.append(Polygon(exterior=Points(ext), holes=tuple(holes)))
    return tuple(out)


def _collect_polygons(geom: object, out: list[ShPolygon]) -> None:
    if isinstance(geom, ShPolygon):
        out.append(geom)
    elif hasattr(geom, "geoms"):
        for g in geom.geoms:
            _collect_polygons(g, out)


def fix_fractured(ring: Arr) -> BaseGeometry:
    """Recover the real polygon (with holes) from a KiCad "fractured" ring.

    KiCad writes an outline with its holes joined by zero-width bridges as ONE self-touching
    ring. ``make_valid`` splits the ring at the touch points and returns the proper polygon with
    holes; the bridges collapse to lines, which are discarded.

    The ``structure`` method (13x faster than GEOS linework) gives the identical point set on
    every WP-102 board and KiCad demo (docs/perf/PROFILE-509.md); only ring start vertices differ.
    ``structure`` can drop holes on rings whose bridges overlap collinear edges, so its result
    is accepted only when its area equals the ring's shoelace area (the bridges have none);
    anything else falls back to ``linework``.
    """
    p = ShPolygon(ring)
    if p.is_valid:
        return p
    g = shapely.make_valid(p, method="structure")
    if (
        isinstance(g, (ShPolygon, MultiPolygon))
        and abs(g.area - abs(_shoelace(ring))) <= 1e-9 * g.area
    ):
        return g
    return shapely.make_valid(p)


def _shoelace(ring: Arr) -> float:
    x, y = ring[:, 0], ring[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


_PAR_MIN_POINTS = 8000  # below this many ring points a thread pool costs more than it saves
_PAR_WORKERS = 2  # default thread cap (docs/TASKS.md); GEOS releases the GIL in make_valid


def fix_fractured_many(rings: Sequence[Arr]) -> list[BaseGeometry]:
    """``fix_fractured`` of every ring, in order. Big batches run on two threads; the result is
    identical to the serial loop (same GEOS call per ring)."""
    if len(rings) < 2 or sum(r.shape[0] for r in rings) < _PAR_MIN_POINTS:
        return [fix_fractured(r) for r in rings]
    with ThreadPoolExecutor(max_workers=_PAR_WORKERS) as ex:
        return list(ex.map(fix_fractured, rings))


def polygon_to_shapely(p: Polygon) -> ShPolygon:
    return ShPolygon(p.exterior.array, [h.array for h in p.holes])


def ring_signed_area(a: Arr) -> float:
    return signed_area(a)


def is_valid_ring(a: Arr) -> bool:
    return bool(a.shape[0] >= 3 and LinearRing(a).is_valid)


# ----------------------------------------------------------------------------------------
# path segments
# ----------------------------------------------------------------------------------------
def make_arc_seg(s: Pt, m: Pt, e: Pt) -> PathSegT:
    """``ArcSeg`` from three points; degrades to a ``LineSeg`` when collinear or inconsistent."""
    arc = arc_from_three(s, m, e)
    if arc is None:
        return LineSeg(start=s, end=e)
    r1 = math.hypot(e[0] - arc.center[0], e[1] - arc.center[1])
    if abs(arc.radius - r1) > 5e-10:  # ArcSeg validator allows 1 nm
        return LineSeg(start=s, end=e)
    return ArcSeg(start=s, end=e, center=arc.center, ccw=arc.ccw)


def seg_points(seg: PathSegT, tol: float) -> Arr:
    if isinstance(seg, LineSeg):
        return np.array([seg.start, seg.end], dtype=np.float64)
    return arc_points(Arc(seg.start, seg.center, seg.end, seg.ccw), tol)


def reverse_seg(seg: PathSegT) -> PathSegT:
    if isinstance(seg, LineSeg):
        return LineSeg(start=seg.end, end=seg.start)
    return ArcSeg(start=seg.end, end=seg.start, center=seg.center, ccw=not seg.ccw)


def path_length(path: Sequence[PathSegT]) -> float:
    total = 0.0
    for s in path:
        if isinstance(s, LineSeg):
            total += math.hypot(s.end[0] - s.start[0], s.end[1] - s.start[1])
        else:
            r = math.hypot(s.start[0] - s.center[0], s.start[1] - s.center[1])
            total += r * abs(Arc(s.start, s.center, s.end, s.ccw).sweep())
    return total
