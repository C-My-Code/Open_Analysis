# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="arg-type,assignment,attr-defined,call-overload,index,return-value"
# mypy: disable-error-code="no-any-return,unused-ignore,import-untyped,union-attr,var-annotated"
"""Primitive-to-shapely conversion: arcs, pad shapes, polygons.

All curves are discretized so that the chord (sagitta) error never exceeds ``tol`` metres.
Polygons from the model are inscribed in the true curve, so areas are slightly low, never high.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt
import shapely
import shapely.affinity
import shapely.geometry.polygon
from shapely import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from oa_model.arrays import Points
from oa_model.geometry import ArcSeg, LineSeg, PathSeg
from oa_model.geometry import Polygon as ModelPolygon
from oa_model.padstack import (
    ChamferedRectShape,
    CircleShape,
    ObroundShape,
    PadShape,
    PolygonShape,
    RectShape,
    RoundRectShape,
    TrapezoidShape,
)

F64 = npt.NDArray[np.float64]


def seg_count(radius: float, sweep: float, tol: float) -> int:
    """Chords needed so an arc of `sweep` radians at `radius` has sagitta <= `tol`."""
    if radius <= 0.0 or sweep == 0.0:
        return 1
    if tol >= radius:
        return max(1, math.ceil(abs(sweep) / (math.pi / 2)))
    step = 2.0 * math.acos(1.0 - tol / radius)
    return max(1, math.ceil(abs(sweep) / step))


def quad_segs(radius: float, tol: float) -> int:
    """`quad_segs` for a round buffer of `radius` (segments per quarter circle)."""
    return max(2, seg_count(radius, math.pi / 2, tol))


def arc_sweep(seg: ArcSeg) -> float:
    """Signed sweep angle in radians (positive counter-clockwise)."""
    a0 = math.atan2(seg.start[1] - seg.center[1], seg.start[0] - seg.center[0])
    a1 = math.atan2(seg.end[1] - seg.center[1], seg.end[0] - seg.center[0])
    two_pi = 2.0 * math.pi
    d = (a1 - a0) % two_pi if seg.ccw else -((a0 - a1) % two_pi)
    if d == 0.0 and seg.start == seg.end:
        d = two_pi if seg.ccw else -two_pi
    return d


def arc_points(seg: ArcSeg, tol: float) -> F64:
    """Polyline (n, 2) through the arc with chord error <= tol; exact end points."""
    r = math.hypot(seg.start[0] - seg.center[0], seg.start[1] - seg.center[1])
    sweep = arc_sweep(seg)
    n = seg_count(r, sweep, tol)
    a0 = math.atan2(seg.start[1] - seg.center[1], seg.start[0] - seg.center[0])
    ang = a0 + np.linspace(0.0, sweep, n + 1)
    pts = np.column_stack((seg.center[0] + r * np.cos(ang), seg.center[1] + r * np.sin(ang)))
    pts[0] = seg.start
    pts[-1] = seg.end
    return pts


def seg_points(seg: PathSeg, tol: float) -> F64:
    """Polyline (n, 2) of one path segment."""
    if isinstance(seg, LineSeg):
        return np.array([seg.start, seg.end], dtype=np.float64)
    return arc_points(seg, tol)


def seg_length(seg: PathSeg) -> float:
    """Exact length of a line or arc segment, metres."""
    if isinstance(seg, LineSeg):
        return math.hypot(seg.end[0] - seg.start[0], seg.end[1] - seg.start[1])
    r = math.hypot(seg.start[0] - seg.center[0], seg.start[1] - seg.center[1])
    return abs(arc_sweep(seg)) * r


def path_points(path: tuple[PathSeg, ...], tol: float) -> F64:
    """Concatenated polyline (n, 2) of a contiguous path."""
    parts = [seg_points(s, tol) for s in path]
    out = [parts[0]]
    out.extend(p[1:] for p in parts[1:])
    return np.vstack(out)


def to_shapely(poly: ModelPolygon) -> BaseGeometry:
    """Model polygon (exterior + holes) to a valid shapely geometry (Polygon or MultiPolygon)."""
    p = Polygon(poly.exterior.array, [h.array for h in poly.holes if len(h) >= 3])
    if not p.is_valid:
        return shapely.make_valid(p)
    return p


def polygons_of(geom: BaseGeometry | None) -> list[Polygon]:
    """All polygon parts of any geometry (empty list for empties / non-areal parts)."""
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    if hasattr(geom, "geoms"):
        out: list[Polygon] = []
        for g in geom.geoms:
            out.extend(polygons_of(g))
        return out
    return []


def as_multipolygon(geom: BaseGeometry | None) -> MultiPolygon:
    """Coerce to MultiPolygon (empty MultiPolygon for empties / non-areal results)."""
    return MultiPolygon(polygons_of(geom))


def to_model_polygons(geom: BaseGeometry, *, min_area: float = 0.0) -> tuple[ModelPolygon, ...]:
    """Shapely geometry to model polygons (exterior CCW, holes CW), deterministic order."""
    out: list[ModelPolygon] = []
    for p in polygons_of(geom):
        if p.area <= min_area:
            continue
        q = shapely.geometry.polygon.orient(p, 1.0)
        out.append(
            ModelPolygon(
                exterior=Points(np.asarray(q.exterior.coords)[:-1]),
                holes=tuple(Points(np.asarray(r.coords)[:-1]) for r in q.interiors),
            )
        )
    out.sort(
        key=lambda m: (float(np.min(m.exterior.array[:, 0])), float(np.min(m.exterior.array[:, 1])))
    )
    return tuple(out)


# ---------------------------------------------------------------------------------------------
# Pad shapes (pad-local frame, centred, unrotated, +y up)
# ---------------------------------------------------------------------------------------------


def _circle_ring(r: float, tol: float) -> F64:
    n = max(12, 4 * math.ceil(seg_count(r, math.pi * 2, tol) / 4))
    a = np.arange(n) * (2.0 * math.pi / n)
    return np.column_stack((r * np.cos(a), r * np.sin(a)))


def _corner_arc(cx: float, cy: float, r: float, a0: float, tol: float) -> F64:
    n = seg_count(r, math.pi / 2, tol)
    a = a0 + np.linspace(0.0, math.pi / 2, n + 1)
    return np.column_stack((cx + r * np.cos(a), cy + r * np.sin(a)))


def _rect_ring(
    w: float,
    h: float,
    radii: tuple[float, ...],
    chamfers: tuple[float, ...],
    tol: float,
) -> F64:
    """Ring of a rectangle; per corner (bl, br, tr, tl) rounded, chamfered or square."""
    hw, hh = w / 2.0, h / 2.0
    pts: list[F64] = []
    # (corner x, corner y, arc start angle, inward sign x, inward sign y)
    spec = (
        (-hw, -hh, math.pi, 1.0, 1.0),
        (hw, -hh, 1.5 * math.pi, -1.0, 1.0),
        (hw, hh, 0.0, -1.0, -1.0),
        (-hw, hh, 0.5 * math.pi, 1.0, -1.0),
    )
    for k, (px, py, a0, sx, sy) in enumerate(spec):
        r, c = radii[k], chamfers[k]
        if c > 0.0:
            # points are ordered counter-clockwise around the ring
            if k == 0:
                pts.append(np.array([[px, py + c], [px + c, py]]))
            elif k == 1:
                pts.append(np.array([[px - c, py], [px, py + c]]))
            elif k == 2:
                pts.append(np.array([[px, py - c], [px - c, py]]))
            else:
                pts.append(np.array([[px + c, py], [px, py - c]]))
        elif r > 0.0:
            pts.append(_corner_arc(px + sx * r, py + sy * r, r, a0, tol))
        else:
            pts.append(np.array([[px, py]]))
    return np.vstack(pts)


def shape_geometry(shape: PadShape, tol: float) -> BaseGeometry:
    """Pad-local copper geometry of a pad shape (Polygon, or MultiPolygon for custom pads)."""
    if isinstance(shape, CircleShape):
        return Polygon(_circle_ring(shape.diameter / 2.0, tol))
    if isinstance(shape, RectShape):
        return shapely.box(-shape.width / 2, -shape.height / 2, shape.width / 2, shape.height / 2)
    if isinstance(shape, RoundRectShape):
        r = min(shape.corner_radius, shape.width / 2, shape.height / 2)
        return Polygon(_rect_ring(shape.width, shape.height, (r,) * 4, (0.0,) * 4, tol))
    if isinstance(shape, ObroundShape):
        r = min(shape.width, shape.height) / 2.0
        return Polygon(_rect_ring(shape.width, shape.height, (r,) * 4, (0.0,) * 4, tol))
    if isinstance(shape, ChamferedRectShape):
        r = min(shape.corner_radius, shape.width / 2, shape.height / 2)
        c = min(shape.chamfer, shape.width, shape.height)
        order = ("bl", "br", "tr", "tl")
        ch = tuple(c if k in shape.corners else 0.0 for k in order)
        ra = tuple(0.0 if k in shape.corners else r for k in order)
        return Polygon(_rect_ring(shape.width, shape.height, ra, ch, tol))
    if isinstance(shape, TrapezoidShape):
        hw, hh = shape.width / 2.0, shape.height / 2.0
        dx, dy = shape.delta_x, shape.delta_y
        # KiCad rect_delta corner table, y flipped to y-up.
        ring = [(-hw - dy, -hh - dx), (-hw + dy, hh + dx), (hw - dy, hh - dx), (hw + dy, -hh + dx)]
        return shapely.geometry.polygon.orient(Polygon(ring), 1.0)
    assert isinstance(shape, PolygonShape)
    parts = [to_shapely(p) for p in shape.polygons]
    return shapely.union_all(parts) if parts else Polygon()


def place(local: BaseGeometry, positions: F64, rotations: F64) -> list[BaseGeometry]:
    """Instantiate pad-local geometry at many positions/rotations.

    Vectorized for simple polygons; per-instance affine transforms for multi-part/holed shapes.
    """
    n = len(positions)
    if n == 0 or local.is_empty:
        return []
    cos, sin = np.cos(rotations), np.sin(rotations)
    if isinstance(local, Polygon) and not local.interiors:
        c = np.asarray(local.exterior.coords)[:-1]
        x = c[None, :, 0] * cos[:, None] - c[None, :, 1] * sin[:, None] + positions[:, 0:1]
        y = c[None, :, 0] * sin[:, None] + c[None, :, 1] * cos[:, None] + positions[:, 1:2]
        return list(shapely.polygons(np.stack((x, y), axis=-1)))
    return [
        shapely.affinity.affine_transform(
            local, [cos[i], -sin[i], sin[i], cos[i], positions[i, 0], positions[i, 1]]
        )
        for i in range(n)
    ]
