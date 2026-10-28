# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""KiCad graphic items (``gr_*``, ``fp_*``, pad primitives) to segments and shapely geometry."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import shapely
from oa_model import LineSeg
from shapely.geometry import LinearRing, LineString, Point
from shapely.geometry import Polygon as ShPolygon

from . import sexpr
from ._geom import (
    MM,
    Arc,
    PathSegT,
    arc_from_three,
    arc_points,
    arc_steps,
    bezier_points,
    circle_points,
    make_arc_seg,
)

Arr = npt.NDArray[np.float64]

GRAPHIC_HEADS = frozenset(
    {
        "gr_line",
        "gr_arc",
        "gr_circle",
        "gr_rect",
        "gr_poly",
        "gr_curve",
        "fp_line",
        "fp_arc",
        "fp_circle",
        "fp_rect",
        "fp_poly",
        "fp_curve",
    }
)


@dataclass(frozen=True)
class Xf:
    """Affine map from KiCad mm (Y down) to model metres (Y up).

    ``X = (a x + b y) * 1e-3 + tx``, ``Y = (c x + d y) * 1e-3 + ty``.
    """

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = -1.0
    tx: float = 0.0
    ty: float = 0.0

    def apply(self, p: Arr) -> Arr:
        out = np.empty_like(p, dtype=np.float64)
        out[:, 0] = (self.a * p[:, 0] + self.b * p[:, 1]) * MM + self.tx + 0.0  # +0.0: no -0.0
        out[:, 1] = (self.c * p[:, 0] + self.d * p[:, 1]) * MM + self.ty + 0.0
        return out

    def pt(self, x: float, y: float) -> tuple[float, float]:
        return (
            (self.a * x + self.b * y) * MM + self.tx + 0.0,
            (self.c * x + self.d * y) * MM + self.ty + 0.0,
        )

    @staticmethod
    def footprint(x_mm: float, y_mm: float, rot_deg: float) -> Xf:
        """Footprint-local frame (stored mm, Y down) to board frame (m, Y up)."""
        th = math.radians(rot_deg)
        ct, st = math.cos(th), math.sin(th)
        # exact values at the axis-aligned angles that dominate real boards
        r = round(rot_deg) % 360
        if abs(rot_deg - round(rot_deg)) < 1e-12:
            ct, st = {0: (1.0, 0.0), 90: (0.0, 1.0), 180: (-1.0, 0.0), 270: (0.0, -1.0)}.get(
                r, (ct, st)
            )
        return Xf(ct, st, st, -ct, x_mm * MM, -y_mm * MM)


FLIP = Xf()  # board / pad-local: only mm -> m and the Y flip


def xy(node: sexpr.Node, key: str) -> tuple[float, float] | None:
    v = sexpr.get_floats(node, key)
    if v is None or len(v) < 2:
        return None
    return v[0], v[1]


def stroke_width(node: sexpr.Node) -> float:
    """Line width in mm from ``(stroke (width w))`` or the legacy ``(width w)``."""
    st = sexpr.child(node, "stroke")
    w = sexpr.get_float(st, "width") if st is not None else None
    if w is None:
        w = sexpr.get_float(node, "width")
    return w if w is not None else 0.0


def is_filled(node: sexpr.Node) -> bool:
    f = sexpr.child(node, "fill")
    if f is None:
        return False
    for v in f[1:]:
        if isinstance(v, str):
            return v.lower() in ("yes", "solid", "true", "hatch", "cross_hatch")
        if isinstance(v, list) and v and v[0] == "type":
            return len(v) > 1 and str(v[1]).lower() not in ("none", "no")
    return True


def pts_xy(pts: sexpr.Node | None, xf: Xf, tol: float) -> Arr:
    """Model-frame points of a ``(pts ...)`` node, arcs inside it discretized."""
    if pts is None:
        return np.zeros((0, 2))
    if len(pts) == 2 and isinstance(pts[1], np.ndarray):
        return xf.apply(pts[1])
    out: list[Arr] = []
    cur: list[tuple[float, float]] = []

    def flush() -> None:
        if cur:
            out.append(np.array(cur, dtype=np.float64))
            cur.clear()

    for c in pts[1:]:
        if not isinstance(c, list) or not c:
            continue
        if c[0] == "xy" and len(c) >= 3:
            cur.append(xf.pt(float(c[1]), float(c[2])))
        elif c[0] == "arc":
            s, m, e = xy(c, "start"), xy(c, "mid"), xy(c, "end")
            if s is None or m is None or e is None:
                continue
            ps, pm, pe = xf.pt(*s), xf.pt(*m), xf.pt(*e)
            arc = arc_from_three(ps, pm, pe)
            if arc is None:
                cur.extend([ps, pe])
            else:
                ap = arc_points(arc, tol)
                # the arc start duplicates the previous xy point
                if cur and np.allclose(cur[-1], ap[0], atol=1e-12):
                    ap = ap[1:]
                cur.extend(map(tuple, ap.tolist()))
    flush()
    return np.vstack(out) if out else np.zeros((0, 2))


# ----------------------------------------------------------------------------------------
# analytic segments (Edge.Cuts chaining)
# ----------------------------------------------------------------------------------------
def graphic_segments(
    node: sexpr.Node, xf: Xf, tol: float
) -> tuple[bool, list[PathSegT]] | None:
    """``(closed, segments)`` for line/arc/circle/rect/poly/curve; ``None`` if unusable."""
    h = node[0]
    kind = h.split("_", 1)[1] if isinstance(h, str) and "_" in h else ""
    if kind == "line":
        s, e = xy(node, "start"), xy(node, "end")
        if s is None or e is None:
            return None
        return False, [LineSeg(start=xf.pt(*s), end=xf.pt(*e))]
    if kind == "arc":
        s, m, e = xy(node, "start"), xy(node, "mid"), xy(node, "end")
        if s is None or m is None or e is None:
            return None
        return False, [make_arc_seg(xf.pt(*s), xf.pt(*m), xf.pt(*e))]
    if kind == "circle":
        c, e = xy(node, "center"), xy(node, "end")
        if c is None or e is None:
            return None
        cx, cy = xf.pt(*c)
        ex, ey = xf.pt(*e)
        r = math.hypot(ex - cx, ey - cy)
        if r <= 0:
            return None
        from oa_model import ArcSeg

        left, right = (cx - r, cy), (cx + r, cy)
        return True, [
            ArcSeg(start=right, end=left, center=(cx, cy), ccw=True),
            ArcSeg(start=left, end=right, center=(cx, cy), ccw=True),
        ]
    if kind == "rect":
        s, e = xy(node, "start"), xy(node, "end")
        if s is None or e is None:
            return None
        corners = [xf.pt(s[0], s[1]), xf.pt(e[0], s[1]), xf.pt(e[0], e[1]), xf.pt(s[0], e[1])]
        return True, [
            LineSeg(start=corners[i], end=corners[(i + 1) % 4]) for i in range(4)
        ]
    if kind == "poly":
        pts = pts_xy(sexpr.child(node, "pts"), xf, tol)
        if pts.shape[0] < 3:
            return None
        return True, [
            LineSeg(
                start=(float(pts[i, 0]), float(pts[i, 1])),
                end=(float(pts[(i + 1) % len(pts), 0]), float(pts[(i + 1) % len(pts), 1])),
            )
            for i in range(len(pts))
        ]
    if kind == "curve":
        c = _curve_pts(node, xf, tol)
        if c is None:
            return None
        return False, [
            LineSeg(
                start=(float(c[i, 0]), float(c[i, 1])),
                end=(float(c[i + 1, 0]), float(c[i + 1, 1])),
            )
            for i in range(len(c) - 1)
        ]
    return None


def _curve_pts(node: sexpr.Node, xf: Xf, tol: float) -> Arr | None:
    pts = pts_xy_raw(sexpr.child(node, "pts"))
    if pts is None or pts.shape[0] != 4:
        return None
    p = xf.apply(pts)
    return bezier_points([(float(a), float(b)) for a, b in p], tol)


def pts_xy_raw(pts: sexpr.Node | None) -> Arr | None:
    if pts is None:
        return None
    if len(pts) == 2 and isinstance(pts[1], np.ndarray):
        return pts[1]
    rows = [(float(c[1]), float(c[2])) for c in pts[1:] if isinstance(c, list) and c[0] == "xy"]
    return np.array(rows, dtype=np.float64) if rows else None


# ----------------------------------------------------------------------------------------
# shapely geometry (copper graphics, custom pad primitives, courtyards)
# ----------------------------------------------------------------------------------------
def _cap_segments(radius: float, tol: float) -> int:
    return int(min(max(arc_steps(radius, math.pi / 2, tol), 2), 64))


def stroke(line: Arr, width_m: float, tol: float) -> shapely.Geometry | None:
    if width_m <= 0 or line.shape[0] < 2:
        return None
    return LineString(line).buffer(width_m / 2.0, quad_segs=_cap_segments(width_m / 2.0, tol))


def graphic_geometry(node: sexpr.Node, xf: Xf, tol: float) -> shapely.Geometry | None:
    """Copper area of one graphic item: filled shapes as polygons, strokes buffered."""
    h = node[0]
    kind = h.split("_", 1)[1] if isinstance(h, str) and "_" in h else ""
    w = stroke_width(node) * MM
    filled = is_filled(node)
    if kind == "line":
        s, e = xy(node, "start"), xy(node, "end")
        if s is None or e is None:
            return None
        return stroke(np.array([xf.pt(*s), xf.pt(*e)]), w, tol)
    if kind == "arc":
        s, m, e = xy(node, "start"), xy(node, "mid"), xy(node, "end")
        if s is None or m is None or e is None:
            return None
        ps, pm, pe = xf.pt(*s), xf.pt(*m), xf.pt(*e)
        arc = arc_from_three(ps, pm, pe)
        line = np.array([ps, pe]) if arc is None else arc_points(arc, tol)
        return stroke(line, w, tol)
    if kind == "circle":
        c, e = xy(node, "center"), xy(node, "end")
        if c is None or e is None:
            return None
        cx, cy = xf.pt(*c)
        ex, ey = xf.pt(*e)
        r = math.hypot(ex - cx, ey - cy)
        if r <= 0:
            return None
        if filled:
            return Point(cx, cy).buffer(r + w / 2.0, quad_segs=_cap_segments(r, tol))
        outer = Point(cx, cy).buffer(r + w / 2.0, quad_segs=_cap_segments(r, tol))
        if w <= 0:
            return None
        if r - w / 2.0 > 0:
            return outer.difference(Point(cx, cy).buffer(r - w / 2.0, quad_segs=_cap_segments(r, tol)))
        return outer
    if kind in ("rect", "poly"):
        if kind == "rect":
            s, e = xy(node, "start"), xy(node, "end")
            if s is None or e is None:
                return None
            ring = xf.apply(np.array([s, (e[0], s[1]), e, (s[0], e[1])], dtype=np.float64))
        else:
            ring = pts_xy(sexpr.child(node, "pts"), xf, tol)
        if ring.shape[0] < 3:
            return None
        poly = ShPolygon(ring)
        if not poly.is_valid:
            poly = shapely.make_valid(poly)
        if filled:
            return poly.buffer(w / 2.0, quad_segs=_cap_segments(max(w / 2.0, tol), tol)) if w > 0 else poly
        if w <= 0:
            return None
        return LinearRing(ring).buffer(w / 2.0, quad_segs=_cap_segments(w / 2.0, tol))
    if kind == "curve":
        c = _curve_pts(node, xf, tol)
        return None if c is None else stroke(c, w, tol)
    return None


def circle_geometry(center: tuple[float, float], radius: float, tol: float) -> shapely.Geometry:
    return ShPolygon(circle_points(center, radius, tol))


__all__ = [
    "FLIP",
    "GRAPHIC_HEADS",
    "Arc",
    "Xf",
    "circle_geometry",
    "graphic_geometry",
    "graphic_segments",
    "is_filled",
    "pts_xy",
    "pts_xy_raw",
    "stroke",
    "stroke_width",
    "xy",
]
