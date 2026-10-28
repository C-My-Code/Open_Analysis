# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Geometry helpers for the IPC-2581 reader (all lengths in metres, angles in radians)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Point
from shapely.geometry import Polygon as ShPolygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from oa_model.geometry import ArcSeg, LineSeg, PathSeg, Polygon
from oa_model.padstack import (
    CircleShape,
    ObroundShape,
    PadShape,
    PolygonShape,
    RectShape,
    RoundRectShape,
)

# A path vertex: (x, y, center|None, clockwise). center is set for arc steps ending at (x, y).
Vertex = tuple[float, float, tuple[float, float] | None, bool]


def local(el: Any) -> str:
    tag = el.tag
    return tag.rpartition("}")[2] if isinstance(tag, str) else ""


def fnum(el: Any, name: str, scale: float, default: float = 0.0) -> float:
    v = el.get(name)
    if v is None or v == "":
        return default
    return float(v) * scale


def arc_steps(radius: float, sweep: float, tol: float) -> int:
    """Number of chords so the sagitta error stays below ``tol``."""
    if radius <= tol:
        return max(2, math.ceil(abs(sweep) / (math.pi / 2)))
    step = 2.0 * math.acos(max(-1.0, min(1.0, 1.0 - tol / radius)))
    return max(2, math.ceil(abs(sweep) / max(step, 1e-3)))


def arc_sweep(
    start: tuple[float, float], end: tuple[float, float], center: tuple[float, float], cw: bool
) -> tuple[float, float, float]:
    """(radius, start_angle, signed sweep). start == end is a full circle."""
    a0 = math.atan2(start[1] - center[1], start[0] - center[0])
    a1 = math.atan2(end[1] - center[1], end[0] - center[0])
    r = math.hypot(start[0] - center[0], start[1] - center[1])
    if math.hypot(end[0] - start[0], end[1] - start[1]) < 1e-12:
        return r, a0, -2.0 * math.pi if cw else 2.0 * math.pi
    d = a1 - a0
    if cw:
        while d >= 0:
            d -= 2.0 * math.pi
    else:
        while d <= 0:
            d += 2.0 * math.pi
    return r, a0, d


def tessellate_arc(
    start: tuple[float, float],
    end: tuple[float, float],
    center: tuple[float, float],
    cw: bool,
    tol: float,
) -> list[tuple[float, float]]:
    """Points along the arc, excluding ``start`` and including ``end``."""
    r, a0, sweep = arc_sweep(start, end, center, cw)
    n = arc_steps(r, sweep, tol)
    pts = [
        (center[0] + r * math.cos(a0 + sweep * k / n), center[1] + r * math.sin(a0 + sweep * k / n))
        for k in range(1, n)
    ]
    pts.append(end)
    return pts


def path_points(verts: list[Vertex], tol: float) -> list[tuple[float, float]]:
    """Discretize a vertex path (first vertex is the start)."""
    pts = [(verts[0][0], verts[0][1])]
    for x, y, c, cw in verts[1:]:
        if c is None:
            pts.append((x, y))
        else:
            pts.extend(tessellate_arc(pts[-1], (x, y), c, cw, tol))
    return pts


def ring_polygon(verts: list[Vertex], tol: float) -> ShPolygon:
    pts = path_points(verts, tol)
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    p = ShPolygon(pts)
    return p if p.is_valid else p.buffer(0)


def stroke(geom: BaseGeometry, width: float, end: str, tol: float) -> BaseGeometry:
    """Buffer a centreline by half the width (IPC ``lineEnd``: ROUND, SQUARE, NONE)."""
    r = width / 2.0
    if r <= 0:
        return ShPolygon()
    cap = {"ROUND": "round", "SQUARE": "square", "NONE": "flat"}.get(end.upper(), "round")
    quad = max(2, arc_steps(r, math.pi / 2, tol))
    return geom.buffer(r, cap_style=cap, quad_segs=quad)  # type: ignore[arg-type]


def circle_geom(d: float, tol: float, cx: float = 0.0, cy: float = 0.0) -> ShPolygon:
    r = d / 2.0
    quad = max(2, arc_steps(r, math.pi / 2, tol)) if r > 0 else 2
    return Point(cx, cy).buffer(r, quad_segs=quad)


def to_model_polygons(geom: BaseGeometry | None) -> list[Polygon]:
    """Shapely geometry to oa_model polygons (exterior CCW, holes CW, rings open)."""
    if geom is None or geom.is_empty:
        return []
    parts: list[ShPolygon]
    if isinstance(geom, ShPolygon):
        parts = [geom]
    elif isinstance(geom, MultiPolygon):
        parts = list(geom.geoms)
    elif hasattr(geom, "geoms"):
        parts = [g for g in geom.geoms if isinstance(g, ShPolygon)]
    else:
        return []
    out: list[Polygon] = []
    for p in parts:
        if p.is_empty or p.area <= 0:
            continue
        p = orient(p, 1.0)
        ext = np.asarray(p.exterior.coords)[:-1]
        holes = tuple(
            Polygon_pts(np.asarray(h.coords)[:-1]) for h in p.interiors if len(h.coords) > 3
        )
        out.append(Polygon(exterior=Polygon_pts(ext), holes=holes))
    return out


def Polygon_pts(a: np.ndarray) -> Any:
    from oa_model.arrays import Points

    return Points(a)


def union(geoms: list[BaseGeometry]) -> BaseGeometry:
    return unary_union(geoms) if geoms else ShPolygon()


def xform_geom(
    g: BaseGeometry, rotation: float, mirror: bool, dx: float, dy: float
) -> BaseGeometry:
    """Mirror (x -> -x), rotate CCW about the origin, then translate."""
    if mirror:
        g = affinity.scale(g, xfact=-1.0, yfact=1.0, origin=(0, 0))
    if rotation:
        g = affinity.rotate(g, rotation, origin=(0, 0), use_radians=True)
    if dx or dy:
        g = affinity.translate(g, dx, dy)
    return g


def pad_shape_from_geom(g: BaseGeometry) -> PadShape | None:
    polys = to_model_polygons(g)
    return PolygonShape(polygons=tuple(polys)) if polys else None


def standard_shape(kind: str, el: Any, s: float) -> PadShape | None:
    """Native pad shapes for the primitives that map one-to-one."""
    if kind == "Circle":
        return CircleShape(diameter=fnum(el, "diameter", s))
    if kind == "RectCenter":
        return RectShape(width=fnum(el, "width", s), height=fnum(el, "height", s))
    if kind == "Oval":
        w, h = fnum(el, "width", s), fnum(el, "height", s)
        return CircleShape(diameter=w) if abs(w - h) < 1e-12 else ObroundShape(width=w, height=h)
    if kind == "RectRound":
        corners = [
            el.get(k, "false").lower() == "true"
            for k in ("upperRight", "upperLeft", "lowerRight", "lowerLeft")
        ]
        if all(corners):
            w, h, r = fnum(el, "width", s), fnum(el, "height", s), fnum(el, "radius", s)
            return RoundRectShape(width=w, height=h, corner_radius=min(r, min(w, h) / 2.0))
    return None


def polygon_prim(kind: str, el: Any, s: float, tol: float) -> BaseGeometry | None:
    """Geometry for the standard primitives without a native pad shape."""
    if kind == "Circle":
        return circle_geom(fnum(el, "diameter", s), tol)
    if kind == "RectCenter":
        w, h = fnum(el, "width", s) / 2, fnum(el, "height", s) / 2
        return ShPolygon([(-w, -h), (w, -h), (w, h), (-w, h)])
    if kind == "Oval":
        w, h = fnum(el, "width", s), fnum(el, "height", s)
        r = min(w, h) / 2.0
        core = LineString([(-(w / 2 - r), -(h / 2 - r)), ((w / 2 - r), (h / 2 - r))])
        if w >= h:
            core = LineString([(-(w / 2 - r), 0), (w / 2 - r, 0)])
        else:
            core = LineString([(0, -(h / 2 - r)), (0, h / 2 - r)])
        return stroke(core, 2 * r, "ROUND", tol)
    if kind == "RectRound":
        w, h, r = fnum(el, "width", s), fnum(el, "height", s), fnum(el, "radius", s)
        base = ShPolygon([(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)])
        r = min(r, min(w, h) / 2.0)
        if r <= 0:
            return base
        rounded = base.buffer(-r).buffer(r, quad_segs=max(2, arc_steps(r, math.pi / 2, tol)))
        flags = {
            "upperRight": (1, 1),
            "upperLeft": (-1, 1),
            "lowerRight": (1, -1),
            "lowerLeft": (-1, -1),
        }
        out = rounded
        for name, (sx, sy) in flags.items():
            if el.get(name, "true").lower() != "true":
                box = ShPolygon(
                    [
                        (sx * (w / 2 - r), sy * (h / 2 - r)),
                        (sx * (w / 2), sy * (h / 2 - r)),
                        (sx * (w / 2), sy * (h / 2)),
                        (sx * (w / 2 - r), sy * (h / 2)),
                    ]
                )
                out = unary_union([out, box])  # type: ignore[assignment]
        return out
    if kind == "RectCorner":
        x0, y0 = fnum(el, "lowerLeftX", s), fnum(el, "lowerLeftY", s)
        x1, y1 = fnum(el, "upperRightX", s), fnum(el, "upperRightY", s)
        return ShPolygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    if kind == "Ellipse":
        w, h = fnum(el, "width", s), fnum(el, "height", s)
        return affinity.scale(circle_geom(1.0, tol / max(w, h, 1e-9)), w, h)
    if kind == "Diamond":
        w, h = fnum(el, "width", s) / 2, fnum(el, "height", s) / 2
        return ShPolygon([(w, 0), (0, h), (-w, 0), (0, -h)])
    if kind in ("Hexagon", "Octagon"):
        n = 6 if kind == "Hexagon" else 8
        d = fnum(el, "length", s) or fnum(el, "diameter", s)
        r = d / 2.0 / math.cos(math.pi / n)
        return ShPolygon(
            [
                (r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n))
                for k in range(n)
            ]
        )
    if kind == "Donut":
        shape = el.get("shape", "ROUND").upper()
        od, idm = fnum(el, "outerDiameter", s), fnum(el, "innerDiameter", s)
        if shape == "SQUARE":
            h = od / 2
            outer: BaseGeometry = ShPolygon([(-h, -h), (h, -h), (h, h), (-h, h)])
        else:
            outer = circle_geom(od, tol)
        return outer.difference(circle_geom(idm, tol))
    return None


__all__ = [
    "ArcSeg",
    "LineSeg",
    "PathSeg",
    "Vertex",
    "arc_steps",
    "circle_geom",
    "fnum",
    "local",
    "pad_shape_from_geom",
    "path_points",
    "polygon_prim",
    "ring_polygon",
    "standard_shape",
    "stroke",
    "tessellate_arc",
    "to_model_polygons",
    "union",
    "xform_geom",
]
