# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Counts and per-layer copper areas of a ``Board`` (same definitions as ``oracle_pcbnew.py``)."""

from __future__ import annotations

import math
from typing import Any

from shapely import affinity
from shapely.geometry import LineString, Point
from shapely.geometry import Polygon as ShPolygon
from shapely.ops import unary_union

from oa_model import Board
from oa_model.geometry import ArcSeg
from oa_model.padstack import PadShape


def _poly(p: Any) -> ShPolygon:
    g = ShPolygon(p.exterior.array, [h.array for h in p.holes])
    return g if g.is_valid else g.buffer(0)


def shape_geom(shape: PadShape) -> Any:
    k = shape.kind
    if k == "circle":
        return Point(0, 0).buffer(shape.diameter / 2, quad_segs=32)
    if k == "rect":
        w, h = shape.width / 2, shape.height / 2
        return ShPolygon([(-w, -h), (w, -h), (w, h), (-w, h)])
    if k == "obround":
        w, h = shape.width, shape.height
        r = min(w, h) / 2
        a = (w / 2 - r, 0) if w >= h else (0, h / 2 - r)
        return LineString([(-a[0], -a[1]), a]).buffer(r, quad_segs=32)
    if k == "roundrect":
        w, h, r = shape.width, shape.height, shape.corner_radius
        base = ShPolygon([(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)])
        return base.buffer(-r).buffer(r, quad_segs=16) if r > 0 else base
    if k == "polygon":
        return unary_union([_poly(p) for p in shape.polygons])
    raise NotImplementedError(k)


def _place(shape: PadShape, pos: tuple[float, float], rot: float) -> Any:
    g = affinity.rotate(shape_geom(shape), rot, origin=(0, 0), use_radians=True)
    return affinity.translate(g, pos[0], pos[1])


def track_geom(t: Any) -> Any:
    pts: list[tuple[float, float]] = [tuple(t.path[0].start)]
    for s in t.path:
        if isinstance(s, ArcSeg):
            r = math.dist(s.start, s.center)
            a0 = math.atan2(s.start[1] - s.center[1], s.start[0] - s.center[0])
            a1 = math.atan2(s.end[1] - s.center[1], s.end[0] - s.center[0])
            d = a1 - a0
            d = d % (2 * math.pi) if s.ccw else -((-d) % (2 * math.pi))
            n = 24
            pts.extend(
                (
                    s.center[0] + r * math.cos(a0 + d * i / n),
                    s.center[1] + r * math.sin(a0 + d * i / n),
                )
                for i in range(1, n + 1)
            )
        else:
            pts.append(tuple(s.end))
    cap = {"round": "round", "square": "square", "flat": "flat"}[t.end_caps]
    return LineString(pts).buffer(t.width / 2, cap_style=cap, quad_segs=16)


def board_stats(b: Board) -> dict[str, Any]:
    cu = [layer.id for layer in b.stackup.layers if layer.kind == "conductor"]
    per: dict[str, dict[str | None, list[Any]]] = {lid: {} for lid in cu}
    pad_by_id = {p.id: p for p in b.pads}
    ps = {p.id: p for p in b.padstacks}
    nets: set[str] = set()

    def add(lid: str, net: str | None, g: Any) -> None:
        if g is not None and not g.is_empty:
            per[lid].setdefault(net, []).append(g)
            if net:
                nets.add(net)

    for t in b.tracks:
        add(t.layer, t.net, track_geom(t))
    for p in b.pads:
        for pl in ps[p.padstack].layers:
            if pl.pad is not None:
                add(pl.layer, p.net, _place(pl.pad, p.position, p.rotation))
    for v in b.vias:
        for pl in ps[v.padstack].layers:
            if pl.pad is not None:
                add(pl.layer, v.net, _place(pl.pad, v.position, 0.0))
    for z in b.zones:
        for f in z.fills:
            for poly in f.polygons:
                add(f.layer, z.net, _poly(poly))
    for s in b.shapes:
        add(s.layer, s.net, _poly(s.polygon))

    area: dict[str, Any] = {}
    for lid, by in per.items():
        total = unary_union([g for gs in by.values() for g in gs]).area
        area[lid] = {
            "total": total,
            "nets": {
                (n or ""): unary_union(gs).area
                for n, gs in sorted(by.items(), key=lambda kv: kv[0] or "")
            },
        }
    vias = {"through": 0, "blind": 0, "buried": 0}
    for v in b.vias:
        vias[
            "through"
            if v.kind == "through"
            else "blind"
            if v.kind in ("blind", "micro")
            else "buried"
        ] += 1
    del pad_by_id
    return {
        "counts": {
            "nets": len(nets),
            "tracks": len(b.tracks),
            "vias": vias,
            "pads": len(b.pads),
            "components": len(b.components),
            "zone_fills": sum(len(z.fills) for z in b.zones),
        },
        "copper_area": area,
    }
