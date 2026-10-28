# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Via and plated-hole helpers: copper layers, barrel extent, stub length.

Conventions (board-model.md section 8):

- The plated barrel runs from the top surface of ``start_layer`` to the bottom surface of
  ``end_layer``.
- A backdrill on ``side="bottom"`` removes barrel copper and pads from the bottom face up to the
  *inner* surface of ``last_removed_layer`` (the barrel ends at that layer's top), unless
  ``depth`` is given, then it ends ``depth`` above the bottom face. ``side="top"`` is symmetric
  (the barrel then starts at the bottom surface of ``last_removed_layer``).
- The stub on one side is the barrel length between the surface of the farthest *used* layer
  facing the barrel end, and the barrel end.
"""

from __future__ import annotations

from collections.abc import Iterable

from oa_geometry.errors import UnknownItem
from oa_model.board import Board
from oa_model.index import BoardIndex
from oa_model.pads import Pad
from oa_model.padstack import Padstack
from oa_model.stackup import ConductorLayer
from oa_model.vias import Via

__all__ = ["barrel_z_range", "pad_copper_layers", "via_copper_layers", "via_stub", "via_stubs"]


def _cidx(idx: BoardIndex, layer_id: str) -> ConductorLayer:
    layer = idx.layer(layer_id)
    if not isinstance(layer, ConductorLayer):
        raise UnknownItem(f"{layer_id} is not a conductor layer", path=layer_id)
    return layer


def _removed(idx: BoardIndex, via: Via) -> set[str]:
    """Conductor layer ids whose barrel copper/pads are removed by backdrills."""
    gone: set[str] = set()
    for bd in via.backdrills:
        last = _cidx(idx, bd.last_removed_layer).index
        for layer in idx.conductor_layers:
            if (bd.side == "top" and layer.index <= last) or (
                bd.side == "bottom" and layer.index >= last
            ):
                gone.add(layer.id)
    return gone


def via_copper_layers(idx: BoardIndex, via: Via, padstack: Padstack) -> tuple[str, ...]:
    """Conductor layers (top to bottom) where the via has a pad: inside its span, a pad in the
    padstack, and not removed by a backdrill."""
    lo = _cidx(idx, via.start_layer).index
    hi = _cidx(idx, via.end_layer).index
    gone = _removed(idx, via)
    has_pad = {pl.layer for pl in padstack.layers if pl.pad is not None}
    return tuple(
        layer.id
        for layer in idx.conductor_layers
        if lo <= layer.index <= hi and layer.id in has_pad and layer.id not in gone
    )


def pad_copper_layers(padstack: Padstack) -> tuple[str, ...]:
    """Layers with copper for a pad, in padstack order."""
    return tuple(pl.layer for pl in padstack.layers if pl.pad is not None)


def pad_is_plated_through(pad: Pad, padstack: Padstack) -> bool:
    """A pad whose plated barrel joins its copper layers."""
    return (
        padstack.drill is not None
        and padstack.drill.kind == "plated"
        and len(pad_copper_layers(padstack)) > 1
    )


def barrel_z_range(board: Board, via: Via) -> tuple[float, float]:
    """(z_bottom, z_top) of the plated barrel after backdrilling, metres."""
    idx = BoardIndex.of(board)
    top = _cidx(idx, via.start_layer).z_top
    bottom = _cidx(idx, via.end_layer).z_bottom
    face_top = board.stackup.layers[0].z_top
    face_bottom = board.stackup.layers[-1].z_bottom
    for bd in via.backdrills:
        last = _cidx(idx, bd.last_removed_layer)
        if bd.side == "bottom":
            bottom = max(bottom, face_bottom + bd.depth if bd.depth is not None else last.z_top)
        else:
            top = min(top, face_top - bd.depth if bd.depth is not None else last.z_bottom)
    return bottom, top


def via_stubs(board: Board, via: Via, used_layers: Iterable[str]) -> tuple[float, float]:
    """(top_stub, bottom_stub) barrel length beyond the used layers [m]."""
    idx = BoardIndex.of(board)
    used = [_cidx(idx, lid) for lid in used_layers]
    if not used:
        raise UnknownItem(f"via {via.id}: no used layers given", path=via.id)
    bottom, top = barrel_z_range(board, via)
    deepest = min(used, key=lambda layer: layer.z_bottom)
    shallowest = max(used, key=lambda layer: layer.z_top)
    return max(0.0, top - shallowest.z_top), max(0.0, deepest.z_bottom - bottom)


def via_stub(board: Board, via: Via, used_layers: Iterable[str]) -> float:
    """Stub length of `via` [m]: the longer of the two barrel remnants beyond `used_layers`."""
    return max(via_stubs(board, via, used_layers))
