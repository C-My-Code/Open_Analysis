# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Copper primitives per layer, unions and spatial indexes.

Tolerance policy: coordinates stay in float64 metres. The model's 1 nm grid is a *comparison*
tolerance (``GEOM_EPS``); unions are not snapped, so the area of a union of disjoint primitives
equals the sum of their areas to floating-point accuracy. Curves have sagitta error <= ``arc_tol``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import numpy.typing as npt
import shapely
from shapely import MultiPolygon, STRtree
from shapely.geometry.base import BaseGeometry

from oa_geometry._shapes import (
    as_multipolygon,
    path_points,
    place,
    quad_segs,
    shape_geometry,
    to_shapely,
)
from oa_geometry.vias import via_copper_layers
from oa_model.board import Board
from oa_model.copper import Track
from oa_model.geometry import LineSeg
from oa_model.index import BoardIndex, ItemRef

__all__ = ["LayerCopper", "LayerIndex", "LayerPrims", "build_layer_prims"]

_CAP: dict[str, Literal["round", "square", "flat"]] = {
    "round": "round",
    "square": "square",
    "flat": "flat",
}


@dataclass
class LayerPrims:
    """All copper primitives of one layer (parallel lists; arrays are built on demand)."""

    geoms: list[BaseGeometry] = field(default_factory=list)
    nets: list[str | None] = field(default_factory=list)
    refs: list[ItemRef] = field(default_factory=list)

    def add(self, geom: BaseGeometry, net: str | None, ref: ItemRef) -> None:
        self.geoms.append(geom)
        self.nets.append(net)
        self.refs.append(ref)

    def extend(
        self, geoms: list[BaseGeometry], nets: list[str | None], refs: list[ItemRef]
    ) -> None:
        self.geoms.extend(geoms)
        self.nets.extend(nets)
        self.refs.extend(refs)


class LayerIndex:
    """STRtree over the copper primitives of one layer; ``query(geom) -> ItemRefs``."""

    def __init__(self, layer: str, prims: LayerPrims) -> None:
        self.layer = layer
        self._geoms = np.empty(len(prims.geoms), dtype=object)
        self._geoms[:] = prims.geoms
        self._refs = prims.refs
        self._nets = prims.nets
        self._tree = STRtree(self._geoms)

    def __len__(self) -> int:
        return len(self._refs)

    def query_indices(
        self, geom: BaseGeometry, predicate: str | None = "intersects"
    ) -> npt.NDArray[np.intp]:
        """Primitive indices whose geometry satisfies `predicate` against `geom`."""
        return np.sort(self._tree.query(geom, predicate=predicate))

    def query(
        self, geom: BaseGeometry, predicate: str | None = "intersects"
    ) -> tuple[ItemRef, ...]:
        """Items on this layer touching `geom` (deduplicated, in insertion order)."""
        seen: dict[ItemRef, None] = {}
        for i in self.query_indices(geom, predicate):
            seen.setdefault(self._refs[int(i)], None)
        return tuple(seen)

    def geometry(self, i: int) -> BaseGeometry:
        return self._geoms[i]

    def ref(self, i: int) -> ItemRef:
        return self._refs[i]

    def net(self, i: int) -> str | None:
        return self._nets[i]

    def nearest(self, geom: BaseGeometry, max_distance: float | None = None) -> ItemRef | None:
        """Item nearest to `geom`, or None when none is within `max_distance`."""
        if len(self) == 0:
            return None
        res = self._tree.query_nearest(geom, max_distance=max_distance)
        if len(res) == 0:
            return None
        return self._refs[int(np.sort(res)[0])]


class LayerCopper:
    """Lazy per-net unions of one layer."""

    def __init__(self, layer: str, prims: LayerPrims) -> None:
        self.layer = layer
        self.prims = prims
        self._by_net: dict[str | None, MultiPolygon] | None = None
        self._all: MultiPolygon | None = None
        self.index_cache: LayerIndex | None = None

    def by_net(self) -> dict[str | None, MultiPolygon]:
        if self._by_net is None:
            groups: dict[str | None, list[BaseGeometry]] = defaultdict(list)
            for g, n in zip(self.prims.geoms, self.prims.nets, strict=True):
                groups[n].append(g)
            order = sorted(groups, key=lambda n: (n is None, n or ""))
            self._by_net = {n: as_multipolygon(shapely.union_all(groups[n])) for n in order}
        return self._by_net

    def union(self, net: str | None = None, *, all_nets: bool = True) -> MultiPolygon:
        """Union of the layer's copper: all nets when `net` is None and `all_nets`, else `net`."""
        if net is None and all_nets:
            if self._all is None:
                self._all = as_multipolygon(shapely.union_all(list(self.by_net().values())))
            return self._all
        return self.by_net().get(net, MultiPolygon())

    def index(self) -> LayerIndex:
        if self.index_cache is None:
            self.index_cache = LayerIndex(self.layer, self.prims)
        return self.index_cache


# ---------------------------------------------------------------------------------------------
# Building primitives
# ---------------------------------------------------------------------------------------------


def _track_geoms(board: Board, tol: float, out: dict[str, LayerPrims]) -> None:
    simple: dict[tuple[str, float, str], list[Track]] = defaultdict(list)
    for t in board.tracks:
        if t.layer not in out:
            continue
        if len(t.path) == 1 and isinstance(t.path[0], LineSeg):
            simple[(t.layer, t.width, t.end_caps)].append(t)
        else:
            pts = path_points(t.path, tol)
            line = shapely.linestrings(pts)
            g = shapely.buffer(
                line,
                t.width / 2.0,
                quad_segs=quad_segs(t.width / 2.0, tol),
                cap_style=_CAP[t.end_caps],
                join_style="round",
            )
            out[t.layer].add(g, t.net, ItemRef("track", t.id))
    for (layer, width, cap), tracks in simple.items():
        coords = np.array(
            [[s.start, s.end] for t in tracks for s in (t.path[0],) if isinstance(s, LineSeg)],
            dtype=np.float64,
        )
        lines = shapely.linestrings(coords)
        degenerate = shapely.length(lines) == 0.0
        r = width / 2.0
        geoms = shapely.buffer(
            lines, r, quad_segs=quad_segs(r, tol), cap_style=_CAP[cap], join_style="round"
        )
        if degenerate.any():
            pts = shapely.points(coords[degenerate, 0, :])
            geoms[degenerate] = shapely.buffer(pts, r, quad_segs=quad_segs(r, tol))
        out[layer].extend(
            list(geoms), [t.net for t in tracks], [ItemRef("track", t.id) for t in tracks]
        )


def _padstack_locals(board: Board, tol: float) -> dict[str, dict[str, BaseGeometry]]:
    locs: dict[str, dict[str, BaseGeometry]] = {}
    for ps in board.padstacks:
        for pl in ps.layers:
            if pl.pad is not None:
                g = shape_geometry(pl.pad, tol)
                if not g.is_empty:
                    locs.setdefault(ps.id, {})[pl.layer] = g
    return locs


def _pad_and_via_geoms(board: Board, tol: float, out: dict[str, LayerPrims]) -> None:
    locs = _padstack_locals(board, tol)
    idx = BoardIndex.of(board)
    # pads
    by_ps: dict[str, list[int]] = defaultdict(list)
    for i, p in enumerate(board.pads):
        by_ps[p.padstack].append(i)
    for ps_id, ids in by_ps.items():
        pos = np.array([board.pads[i].position for i in ids], dtype=np.float64)
        rot = np.array([board.pads[i].rotation for i in ids], dtype=np.float64)
        for layer, local in locs.get(ps_id, {}).items():
            if layer not in out:
                continue
            geoms = place(local, pos, rot)
            out[layer].extend(
                geoms,
                [board.pads[i].net for i in ids],
                [ItemRef("pad", board.pads[i].id) for i in ids],
            )
    # vias: layers depend on span and backdrills, so group by (padstack, layer)
    ps_map = {p.id: p for p in board.padstacks}
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, v in enumerate(board.vias):
        ps = ps_map.get(v.padstack)
        if ps is None:
            continue
        for lid in via_copper_layers(idx, v, ps):
            groups[(v.padstack, lid)].append(i)
    for (ps_id, layer), ids in groups.items():
        local = locs.get(ps_id, {}).get(layer)
        if local is None or layer not in out:
            continue
        pos = np.array([board.vias[i].position for i in ids], dtype=np.float64)
        geoms = place(local, pos, np.zeros(len(ids)))
        out[layer].extend(
            geoms,
            [board.vias[i].net for i in ids],
            [ItemRef("via", board.vias[i].id) for i in ids],
        )


def _zone_and_shape_geoms(board: Board, out: dict[str, LayerPrims]) -> None:
    for z in board.zones:
        if z.keepout is not None:
            continue
        for fill in z.fills:
            if fill.layer not in out:
                continue
            for poly in fill.polygons:
                g = to_shapely(poly)
                if not g.is_empty:
                    out[fill.layer].add(g, z.net, ItemRef("zone", z.id))
    for s in board.shapes:
        if s.layer in out:
            g = to_shapely(s.polygon)
            if not g.is_empty:
                out[s.layer].add(g, s.net, ItemRef("shape", s.id))


def build_layer_prims(board: Board, tol: float) -> dict[str, LayerPrims]:
    """Copper primitives for every conductor layer: tracks (buffered, caps by `end_caps`), pads
    (rotated parametric shapes), via pads, zone fills (actual copper) and graphic shapes."""
    idx = BoardIndex.of(board)
    out: dict[str, LayerPrims] = {layer.id: LayerPrims() for layer in idx.conductor_layers}
    _track_geoms(board, tol, out)
    _pad_and_via_geoms(board, tol, out)
    _zone_and_shape_geoms(board, out)
    return out
