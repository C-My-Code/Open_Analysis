# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pad and via shapes: KiCad pad parameters to :mod:`oa_model.padstack` shapes."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import shapely
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from oa_model import Polygon
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

from . import sexpr
from ._geom import MM, from_shapely
from .graphics import FLIP, graphic_geometry

_CORNER = {"top_left": "tl", "top_right": "tr", "bottom_left": "bl", "bottom_right": "br"}
_ROT90_CORNER = {"tl": "tr", "tr": "br", "br": "bl", "bl": "tl"}


@dataclass(frozen=True)
class ShapeParams:
    """Pad shape parameters, all in KiCad mm; ``None`` means "inherit"."""

    shape: str | None = None
    w: float | None = None
    h: float | None = None
    rratio: float | None = None
    cratio: float | None = None
    corners: tuple[str, ...] | None = None
    dx: float | None = None
    dy: float | None = None
    anchor: str | None = None
    prims: sexpr.Node | None = field(default=None, compare=False, hash=False)

    def merged(self, over: ShapeParams) -> ShapeParams:
        kw = {
            k: getattr(over, k) for k in self.__dataclass_fields__ if getattr(over, k) is not None
        }
        return replace(self, **kw)


def params_from_node(node: sexpr.Node, shape: str | None = None) -> ShapeParams:
    """Read the shape fields of a ``(pad ...)`` or a padstack ``(layer ...)`` node."""
    size = sexpr.get_floats(node, "size")
    w = h = None
    if size:
        w = size[0]
        h = size[1] if len(size) > 1 else size[0]
    delta = sexpr.get_floats(node, "rect_delta")
    ch = sexpr.child(node, "chamfer")
    corners = None
    if ch is not None:
        corners = tuple(_CORNER[a] for a in ch[1:] if isinstance(a, str) and a in _CORNER)
    opts = sexpr.child(node, "options")
    anchor = sexpr.get_str(opts, "anchor") if opts is not None else None
    return ShapeParams(
        shape=shape or sexpr.get_str(node, "shape"),
        w=w,
        h=h,
        rratio=sexpr.get_float(node, "roundrect_rratio"),
        cratio=sexpr.get_float(node, "chamfer_ratio"),
        corners=corners,
        dx=delta[0] if delta else None,
        dy=delta[1] if delta and len(delta) > 1 else None,
        anchor=anchor,
        prims=sexpr.child(node, "primitives"),
    )


def make_shape(p: ShapeParams, tol: float) -> PadShape | None:
    """Model shape in the pad-local frame, or ``None`` when the pad has no copper."""
    no_size = p.w is None or p.h is None or p.w <= 0 or p.h <= 0
    if no_size and (p.shape != "custom" or p.prims is None):  # custom: primitives only
        return None
    w = (p.w or 0.0) * MM
    h = (p.h or 0.0) * MM
    s = p.shape
    if s == "circle":
        return CircleShape(diameter=w)
    if s == "rect":
        return RectShape(width=w, height=h)
    if s == "oval":
        return CircleShape(diameter=w) if abs(w - h) < 1e-12 else ObroundShape(width=w, height=h)
    if s == "chamfered_rect" or (s == "roundrect" and p.cratio and p.corners):
        c = (p.cratio or 0.0) * min(w, h)
        r = (p.rratio or 0.0) * min(w, h)
        corners = p.corners if p.corners is not None else ()
        if c <= 0 or not corners:
            return (
                RoundRectShape(width=w, height=h, corner_radius=r)
                if r > 0
                else RectShape(width=w, height=h)
            )
        return ChamferedRectShape(width=w, height=h, chamfer=c, corners=corners, corner_radius=r)  # type: ignore[arg-type]
    if s == "roundrect":
        r = (p.rratio or 0.0) * min(w, h)
        if r <= 0:
            return RectShape(width=w, height=h)
        return RoundRectShape(width=w, height=h, corner_radius=r)
    if s == "trapezoid":
        return TrapezoidShape(
            width=w, height=h, delta_x=(p.dx or 0.0) * MM, delta_y=(p.dy or 0.0) * MM
        )
    if s == "custom":
        return _custom_shape(p, tol)
    return None


def _custom_shape(p: ShapeParams, tol: float) -> PadShape | None:
    """Anchor union primitives, as polygons in the pad-local frame."""
    geoms: list[BaseGeometry] = []
    w = (p.w or 0.0) * MM
    h = (p.h or 0.0) * MM
    if w > 0 and h > 0:
        if (p.anchor or "circle") == "rect":
            geoms.append(shapely.box(-w / 2, -h / 2, w / 2, h / 2))
        else:
            geoms.append(shapely.Point(0, 0).buffer(w / 2, quad_segs=16))
    if p.prims is not None:
        for prim in p.prims[1:]:
            if isinstance(prim, list) and prim and str(prim[0]).startswith("gr_"):
                g = graphic_geometry(prim, FLIP, tol)
                if g is not None and not g.is_empty:
                    geoms.append(g)
    if not geoms:
        return None
    u = unary_union(geoms)
    polys = from_shapely(u)
    if not polys:
        return None
    return PolygonShape(polygons=polys)


def grow_shape(shape: PadShape | None, margin: float) -> PadShape | None:
    """Solder-mask opening: the copper shape expanded by ``margin`` (metres). Simple shapes only."""
    if shape is None:
        return None
    m2 = 2.0 * margin
    if isinstance(shape, CircleShape):
        return CircleShape(diameter=max(shape.diameter + m2, 0.0))
    if isinstance(shape, RectShape):
        return RectShape(width=max(shape.width + m2, 0.0), height=max(shape.height + m2, 0.0))
    if isinstance(shape, RoundRectShape):
        return RoundRectShape(
            width=max(shape.width + m2, 0.0),
            height=max(shape.height + m2, 0.0),
            corner_radius=max(shape.corner_radius + margin, 0.0),
        )
    if isinstance(shape, ObroundShape):
        return ObroundShape(width=max(shape.width + m2, 0.0), height=max(shape.height + m2, 0.0))
    return None


def rotate_shape_90(shape: PadShape | None) -> PadShape | None:
    """Re-express a shape in a pad frame rotated by +90 degrees (x' = y, y' = -x)."""
    if shape is None or isinstance(shape, CircleShape):
        return shape
    if isinstance(shape, RectShape):
        return RectShape(width=shape.height, height=shape.width)
    if isinstance(shape, ObroundShape):
        return ObroundShape(width=shape.height, height=shape.width)
    if isinstance(shape, RoundRectShape):
        return RoundRectShape(
            width=shape.height, height=shape.width, corner_radius=shape.corner_radius
        )
    if isinstance(shape, ChamferedRectShape):
        return ChamferedRectShape(
            width=shape.height,
            height=shape.width,
            chamfer=shape.chamfer,
            corners=tuple(_ROT90_CORNER[c] for c in shape.corners),  # type: ignore[misc]
            corner_radius=shape.corner_radius,
        )
    if isinstance(shape, TrapezoidShape):
        return TrapezoidShape(
            width=shape.height, height=shape.width, delta_x=shape.delta_y, delta_y=shape.delta_x
        )
    if isinstance(shape, PolygonShape):
        import numpy as np

        from oa_model import Points
        from oa_model.geometry import orient_ring

        def rot(pts: Points) -> Points:
            a = pts.array
            return Points(np.column_stack((a[:, 1], -a[:, 0])))

        # a proper rotation keeps orientation; re-normalize defensively
        return PolygonShape(
            polygons=tuple(
                Polygon(
                    exterior=orient_ring(rot(p.exterior), True),
                    holes=tuple(orient_ring(rot(h), False) for h in p.holes),
                )
                for p in shape.polygons
            )
        )
    return shape
