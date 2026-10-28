# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``BoardGeometry``: the derived-geometry service (interfaces.md section 2)."""

from __future__ import annotations

import math
import weakref
from typing import TYPE_CHECKING, Any

import shapely
from shapely import MultiPolygon, Polygon

from oa_geometry._shapes import as_multipolygon, to_shapely
from oa_geometry.copper import LayerCopper, LayerIndex, build_layer_prims
from oa_geometry.errors import UnknownItem
from oa_geometry.models import (
    DrillGeom,
    Island,
    NetTopology,
    ReferenceMap,
    RegionCut,
    RegionSpec,
    ResolvedTerminal,
    RoutedSegment,
    XsSample,
)
from oa_geometry.vias import via_stub
from oa_model.board import Board
from oa_model.index import BoardIndex
from oa_model.setup.ports import Terminal
from oa_model.stackup import ConductorLayer
from oa_model.vias import Via

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from oa_model.xsection import CrossSection

_BUILT: dict[int, tuple[weakref.ref[Board], dict[float, BoardGeometry]]] = {}


class BoardGeometry:
    """Derived geometry of a ``Board``: copper unions, indexes, connectivity, return paths,
    cross-section sampling and region cuts. Build with ``BoardGeometry.build(board)``.

    The object is a pure function of the board (and ``arc_tol``): every result is memoized and
    safe to reuse. ``build`` returns the same object for the same ``Board`` instance.
    """

    board: Board

    def __init__(self, board: Board, arc_tol: float) -> None:
        self.board = board
        self.arc_tol = arc_tol
        self.idx = BoardIndex.of(board)
        self._copper: dict[str, LayerCopper] = {
            lid: LayerCopper(lid, prims) for lid, prims in build_layer_prims(board, arc_tol).items()
        }
        self._memo: dict[Any, Any] = {}
        self._net_kind = {n.id: n.kind for n in board.nets}

    @classmethod
    def build(cls, board: Board, *, arc_tol: float = 1e-6) -> BoardGeometry:
        """Build (or fetch the cached) geometry of `board`.

        `arc_tol` is the maximum chord (sagitta) error of discretized arcs and round pad/cap
        shapes [m].
        """
        key = id(board)
        hit = _BUILT.get(key)
        if hit is not None and hit[0]() is board and arc_tol in hit[1]:
            return hit[1][arc_tol]
        geo = cls(board, arc_tol)
        if hit is not None and hit[0]() is board:
            hit[1][arc_tol] = geo
        else:

            def _drop(_: object, key: int = key) -> None:
                _BUILT.pop(key, None)

            _BUILT[key] = (weakref.ref(board, _drop), {arc_tol: geo})
        return geo

    # ------------------------------------------------------------------ helpers
    def _memoized(self, key: Any, fn: Any) -> Any:
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]

    def layer_ids(self) -> tuple[str, ...]:
        """Conductor layer ids, top to bottom."""
        return tuple(layer.id for layer in self.idx.conductor_layers)

    def conductor(self, layer: str) -> ConductorLayer:
        """The ``ConductorLayer`` of `layer` (raises ``UnknownItem`` for other ids)."""
        try:
            lyr = self.idx.layer(layer)
        except KeyError:
            raise UnknownItem(f"unknown layer {layer!r}", path=layer) from None
        if not isinstance(lyr, ConductorLayer):
            raise UnknownItem(f"{layer!r} is not a conductor layer", path=layer)
        return lyr

    def net_kind(self, net: str | None) -> str:
        """Net kind ('signal', 'power', 'ground', 'unknown'); 'unknown' for None/missing."""
        return self._net_kind.get(net, "unknown") if net is not None else "unknown"

    def _layer(self, layer: str) -> LayerCopper:
        self.conductor(layer)
        return self._copper[layer]

    @property
    def content_hash(self) -> str:
        """``board.content_hash()``, computed once (it is not cheap on large boards)."""
        return str(self._memoized("hash", self.board.content_hash))

    # ------------------------------------------------------------------ copper
    def copper(self, layer: str, net: str | None = None) -> MultiPolygon:
        """Union of the copper on `layer` of `net`; all nets when `net` is None.

        Tracks are buffered (cap style per ``Track.end_caps``), pads and via pads come from
        their parametric shapes with rotation, zone fills are the actual copper, and graphic
        shapes are included. Barrels and drills are not subtracted.
        """
        return self._layer(layer).union(net)

    def copper_by_net(self, layer: str) -> Mapping[str | None, MultiPolygon]:
        """Per-net copper unions of `layer` (key None: copper without a net)."""
        return dict(self._layer(layer).by_net())

    def outline(self) -> Polygon:
        """The board outline (exterior and cutouts as holes)."""
        poly = to_shapely(self.board.outline.polygon)
        if isinstance(poly, Polygon):
            return poly
        parts = sorted(as_multipolygon(poly).geoms, key=lambda p: -p.area)
        return parts[0]

    def outline_all(self) -> MultiPolygon:
        """Main outline plus any panel outlines (``BoardOutline.extra_boards``)."""
        parts = [self.outline(), *(to_shapely(p) for p in self.board.outline.extra_boards)]
        return as_multipolygon(shapely.union_all(parts))

    def voids(self, layer: str, net: str) -> MultiPolygon:
        """Outline minus the copper of `net` on `layer`: splits, antipads, slots, other nets."""
        key = ("voids", layer, net)
        return self._memoized(
            key,
            lambda: as_multipolygon(self.outline_all().difference(self.copper(layer, net))),
        )

    def strtree(self, layer: str) -> LayerIndex:
        """Spatial index over the copper primitives of `layer`; ``query(geom) -> ItemRefs``."""
        return self._layer(layer).index()

    def drills(self) -> tuple[DrillGeom, ...]:
        """All drilled holes (vias and pad drills), in board order."""

        def make() -> tuple[DrillGeom, ...]:
            out: list[DrillGeom] = []
            order = {lid: i for i, lid in enumerate(self.layer_ids())}
            for v in self.board.vias:
                ps = self.idx.padstack(v.padstack)
                if ps.drill is None:
                    continue
                out.append(
                    DrillGeom(
                        id=f"via:{v.id}",
                        owner=("via", v.id),  # type: ignore[arg-type]
                        position=v.position,
                        diameter=ps.drill.diameter,
                        kind=ps.drill.kind,
                        net=v.net,
                        start_layer=v.start_layer,
                        end_layer=v.end_layer,
                    )
                )
            for p in self.board.pads:
                ps = self.idx.padstack(p.padstack)
                if ps.drill is None:
                    continue
                layers = sorted(
                    (pl.layer for pl in ps.layers if pl.pad is not None),
                    key=lambda lid: order.get(lid, 0),
                ) or [ps.layers[0].layer]
                c, s = math.cos(p.rotation), math.sin(p.rotation)
                ox, oy = ps.drill.offset
                out.append(
                    DrillGeom(
                        id=f"pad:{p.id}",
                        owner=("pad", p.id),  # type: ignore[arg-type]
                        position=(p.position[0] + c * ox - s * oy, p.position[1] + s * ox + c * oy),
                        diameter=ps.drill.diameter,
                        slot_length=ps.drill.slot_length if ps.drill.shape == "slot" else None,
                        rotation=p.rotation,
                        kind=ps.drill.kind,
                        net=p.net,
                        start_layer=layers[0],
                        end_layer=layers[-1],
                    )
                )
            return tuple(out)

        return self._memoized("drills", make)  # type: ignore[no-any-return]

    def via_stub(self, via: Via | str, used_layers: Iterable[str]) -> float:
        """Barrel stub length of `via` beyond `used_layers` [m] (see ``oa_geometry.vias``)."""
        v = self.idx.via(via) if isinstance(via, str) else via
        return via_stub(self.board, v, used_layers)

    # ------------------------------------------------------------------ connectivity
    def topology(self, net: str, *, root: str | None = None) -> NetTopology:
        """Graph of `net`; rooted at pad `root` (an item id) or the lowest-refdes pad."""
        from oa_geometry import topology as _t

        return _t.net_topology(self, net, root)

    def islands(self, net: str) -> tuple[Island, ...]:
        """Disconnected copper groups of `net`, largest first."""
        from oa_geometry import topology as _t

        return _t.net_islands(self, net)

    # ------------------------------------------------------------------ return path
    def reference_map(self, *, k_buffer: float = 3.0) -> ReferenceMap:
        """Per-segment reference spans and via transitions (report 07 section 3.1)."""
        from oa_geometry import refmap as _r

        return _r.reference_map(self, k_buffer)

    # ------------------------------------------------------------------ sampling
    def segments(self, net: str, *, root: str | None = None) -> tuple[RoutedSegment, ...]:
        """Routed segments of `net`, oriented away from the topology root and ordered by
        ``s_start`` (the root is pad/via `root`, default the lowest-refdes pad)."""
        from oa_geometry import topology as _t

        return _t.net_segments(self, net, root)

    def cross_section_at(
        self,
        seg: RoutedSegment,
        s: float,
        *,
        window: float | None = None,
        include_aggressors: bool = True,
        physics_temp: float | None = None,
    ) -> CrossSection:
        """Canonical cross-section perpendicular to `seg` at `s` metres along it."""
        from oa_geometry import sampling as _s

        return _s.cross_section_at(
            self,
            seg,
            s,
            window=window,
            include_aggressors=include_aggressors,
            physics_temp=physics_temp,
        )

    def sample_net(
        self, net: str | tuple[str, str], *, ds: float | None = None
    ) -> tuple[XsSample, ...]:
        """Cross-sections along `net` (or a differential pair, P first) with events."""
        from oa_geometry import sampling as _s

        return _s.sample_net(self, net, ds=ds)

    # ------------------------------------------------------------------ regions
    def cut_region(self, spec: RegionSpec) -> RegionCut:
        """Extract a 3D region (``Structure3D``) and the traces crossing its boundary."""
        from oa_geometry import region as _g

        return _g.cut_region(self, spec)

    def resolve_terminal(
        self, t: Terminal, *, near: tuple[float, float] | None = None, layer: str | None = None
    ) -> ResolvedTerminal:
        """Resolve a port terminal to geometry. ``nearest_reference`` terminals need `near`
        (the positive terminal's position) and optionally its `layer`."""
        from oa_geometry import region as _g

        return _g.resolve_terminal(self, t, near=near, layer=layer)
