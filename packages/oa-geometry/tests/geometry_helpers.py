# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Board-editing helpers for the geometry tests (variants of the oa_testkit builders)."""

from __future__ import annotations

import itertools
from collections.abc import Sequence

from oa_model.arrays import Points
from oa_model.board import Board
from oa_model.copper import Track, Zone, ZoneFill
from oa_model.geometry import ArcSeg, LineSeg, Polygon
from oa_model.nets import Net
from oa_testkit.builders import rect_ring

NM = 1e-9


def poly(exterior: Sequence[tuple[float, float]], *holes: Sequence[tuple[float, float]]) -> Polygon:
    return Polygon(exterior=Points(exterior), holes=tuple(Points(h) for h in holes))


def track(id_: str, net: str, layer: str, width: float, *pts: tuple[float, float]) -> Track:
    return Track(
        id=id_,
        net=net,
        layer=layer,
        width=width,
        path=tuple(LineSeg(start=a, end=b) for a, b in itertools.pairwise(pts)),
    )


def arc_track(
    id_: str,
    net: str,
    layer: str,
    width: float,
    start: tuple[float, float],
    end: tuple[float, float],
    center: tuple[float, float],
    ccw: bool = True,
) -> Track:
    return Track(
        id=id_,
        net=net,
        layer=layer,
        width=width,
        path=(ArcSeg(start=start, end=end, center=center, ccw=ccw),),
    )


def zone(id_: str, net: str, layer: str, *polys: Polygon) -> Zone:
    return Zone(
        id=id_,
        net=net,
        layers=(layer,),
        outline=polys[0],
        fills=(ZoneFill(layer=layer, polygons=tuple(polys)),),
    )


def with_items(
    board: Board,
    *,
    tracks: Sequence[Track] | None = None,
    extra_tracks: Sequence[Track] = (),
    zones: Sequence[Zone] | None = None,
    extra_zones: Sequence[Zone] = (),
    extra_nets: Sequence[Net] = (),
    vias: Sequence | None = None,
    pads: Sequence | None = None,
) -> Board:
    """Copy of `board` with replaced/extended item tuples."""
    upd: dict[str, object] = {}
    upd["tracks"] = tuple(tracks if tracks is not None else board.tracks) + tuple(extra_tracks)
    upd["zones"] = tuple(zones if zones is not None else board.zones) + tuple(extra_zones)
    if extra_nets:
        upd["nets"] = (*board.nets, *extra_nets)
    if vias is not None:
        upd["vias"] = tuple(vias)
    if pads is not None:
        upd["pads"] = tuple(pads)
    return board.model_copy(update=upd)


__all__ = ["NM", "arc_track", "poly", "rect_ring", "track", "with_items", "zone"]
