# SPDX-License-Identifier: GPL-3.0-or-later
"""BoardIndex: read-only lookup tables built once per Board (board-model.md section 13)."""

from __future__ import annotations

import weakref
from typing import Any, Literal, NamedTuple

from oa_model.board import Board, Component
from oa_model.copper import CopperShape, Track, Zone
from oa_model.nets import Net
from oa_model.pads import Pad
from oa_model.padstack import Padstack
from oa_model.stackup import ConductorLayer, StackLayer
from oa_model.vias import Via

ItemKind = Literal["pad", "via", "track", "zone", "shape"]


class ItemRef(NamedTuple):
    """Reference to a board item by kind and stable id."""

    kind: ItemKind
    id: str


_CACHE: dict[int, tuple[weakref.ref[Board], BoardIndex]] = {}


class BoardIndex:
    """Lookup tables by stable key: net name, layer id, padstack id, item id, refdes."""

    def __init__(self, board: Board) -> None:
        self.board = board
        self._layers: dict[str, StackLayer] = {layer.id: layer for layer in board.stackup.layers}
        self.conductor_layers: tuple[ConductorLayer, ...] = tuple(
            layer for layer in board.stackup.layers if isinstance(layer, ConductorLayer)
        )
        self._nets = {n.id: n for n in board.nets}
        self._padstacks = {p.id: p for p in board.padstacks}
        self._pads = {p.id: p for p in board.pads}
        self._vias = {v.id: v for v in board.vias}
        self._components = {c.refdes: c for c in board.components}
        self._tracks = {t.id: t for t in board.tracks}
        self._zones = {z.id: z for z in board.zones}
        self._shapes = {s.id: s for s in board.shapes}
        by_net: dict[str, list[ItemRef]] = {}
        by_layer: dict[str, list[ItemRef]] = {}

        def add(table: dict[str, list[ItemRef]], key: str | None, ref: ItemRef) -> None:
            if key is not None:
                table.setdefault(key, []).append(ref)

        padstack_layers = {
            p.id: [pl.layer for pl in p.layers if pl.pad is not None] for p in board.padstacks
        }
        cidx = {layer.id: layer.index for layer in self.conductor_layers}
        for pad in board.pads:
            r = ItemRef("pad", pad.id)
            add(by_net, pad.net, r)
            for lid in padstack_layers.get(pad.padstack, ()):
                add(by_layer, lid, r)
        for via in board.vias:
            r = ItemRef("via", via.id)
            add(by_net, via.net, r)
            if via.start_layer in cidx and via.end_layer in cidx:
                for layer in self.conductor_layers:
                    if cidx[via.start_layer] <= layer.index <= cidx[via.end_layer]:
                        add(by_layer, layer.id, r)
        for track in board.tracks:
            r = ItemRef("track", track.id)
            add(by_net, track.net, r)
            add(by_layer, track.layer, r)
        for zone in board.zones:
            r = ItemRef("zone", zone.id)
            add(by_net, zone.net, r)
            for lid in zone.layers:
                add(by_layer, lid, r)
        for shape in board.shapes:
            r = ItemRef("shape", shape.id)
            add(by_net, shape.net, r)
            add(by_layer, shape.layer, r)
        self._by_net = {k: tuple(v) for k, v in by_net.items()}
        self._by_layer = {k: tuple(v) for k, v in by_layer.items()}

    @classmethod
    def of(cls, board: Board) -> BoardIndex:
        """Cached index for `board` (the cache entry dies with the board object)."""
        key = id(board)
        hit = _CACHE.get(key)
        if hit is not None and hit[0]() is board:
            return hit[1]
        idx = cls(board)

        def _drop(_: object, key: int = key, cache: dict[int, Any] = _CACHE) -> None:
            cache.pop(key, None)

        _CACHE[key] = (weakref.ref(board, _drop), idx)
        return idx

    def net(self, id: str) -> Net:
        return self._nets[id]

    def layer(self, id: str) -> StackLayer:
        return self._layers[id]

    def padstack(self, id: str) -> Padstack:
        return self._padstacks[id]

    def pad(self, id: str) -> Pad:
        return self._pads[id]

    def via(self, id: str) -> Via:
        return self._vias[id]

    def track(self, id: str) -> Track:
        return self._tracks[id]

    def zone(self, id: str) -> Zone:
        return self._zones[id]

    def shape(self, id: str) -> CopperShape:
        return self._shapes[id]

    def component(self, refdes: str) -> Component:
        return self._components[refdes]

    def items_by_net(self, net: str) -> tuple[ItemRef, ...]:
        return self._by_net.get(net, ())

    def items_by_layer(self, layer: str) -> tuple[ItemRef, ...]:
        return self._by_layer.get(layer, ())
