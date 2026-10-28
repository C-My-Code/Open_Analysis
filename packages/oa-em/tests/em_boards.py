# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Test boards for oa-em derived from the oa_testkit builders."""

from __future__ import annotations

from oa_model.arrays import Points
from oa_model.board import Board
from oa_model.copper import ZoneFill
from oa_model.geometry import LineSeg, Polygon
from oa_model.nets import Net
from oa_testkit import builders as bld


def _shift(p: tuple[float, float], dy: float) -> tuple[float, float]:
    return (p[0], p[1] + dy)


def diff_via_board(pitch: float = 0.55e-3) -> Board:
    """``via_transition_board`` with a second signal net SIG_N routed beside SIG (a differential
    L1 -> L4 via transition): tracks, pads and the signal via are copied and the L2/L3
    antipads follow."""
    base = bld.via_transition_board()
    xv = 25e-3 / 2.0
    tracks = []
    for t in base.tracks:
        seg = t.path[0]
        tracks.append(
            t.model_copy(
                update={
                    "path": (LineSeg(start=_shift(seg.start, pitch), end=_shift(seg.end, pitch)),)
                }
            )
        )
        tracks.append(
            t.model_copy(
                update={
                    "id": t.id.replace("SIG", "SIG_N"),
                    "net": "SIG_N",
                    "path": (
                        LineSeg(start=_shift(seg.start, -pitch), end=_shift(seg.end, -pitch)),
                    ),
                }
            )
        )
    pads = []
    for p in base.pads:
        pads.append(p.model_copy(update={"position": _shift(p.position, pitch)}))
        pads.append(
            p.model_copy(
                update={
                    "id": p.id.replace(".1", ".2"),
                    "number": "2",
                    "net": "SIG_N",
                    "position": _shift(p.position, -pitch),
                }
            )
        )
    comps = [
        c.model_copy(update={"pads": (*c.pads, c.pads[0].replace(".1", ".2"))})
        for c in base.components
    ]
    vias = []
    for v in base.vias:
        if v.net == "SIG":
            vias.append(v.model_copy(update={"position": _shift(v.position, pitch)}))
            vias.append(
                v.model_copy(
                    update={
                        "id": "via:SIG_N:1",
                        "net": "SIG_N",
                        "position": _shift(v.position, -pitch),
                    }
                )
            )
        else:
            vias.append(v)
    ring = bld.rect_ring(-5e-3, -5e-3, 30e-3, 5e-3)
    holes = tuple(
        Points(bld.circle_ring(xv, y, 0.5e-3, 32, clockwise=True)) for y in (pitch, -pitch)
    )
    plane = Polygon(exterior=Points(ring), holes=holes)
    zones = [
        z.model_copy(update={"fills": (ZoneFill(layer=z.layers[0], polygons=(plane,)),)})
        for z in base.zones
    ]
    return base.model_copy(
        update={
            "tracks": tuple(tracks),
            "pads": tuple(pads),
            "components": tuple(comps),
            "vias": tuple(vias),
            "zones": tuple(zones),
            "nets": (*base.nets, Net(id="SIG_N", kind="signal", net_class="Z50")),
        }
    )
