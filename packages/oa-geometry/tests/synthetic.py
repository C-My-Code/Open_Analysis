# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Large synthetic boards for performance tests (deterministic)."""

from __future__ import annotations

import numpy as np

from oa_model.arrays import Points
from oa_model.copper import Track, Zone, ZoneFill
from oa_model.geometry import LineSeg, Polygon
from oa_model.nets import Net
from oa_model.pads import Pad
from oa_model.padstack import CircleShape, Drill, Padstack, PadstackLayer, RectShape
from oa_model.vias import Via
from oa_testkit.builders import _assemble, _outline, circle_ring, make_stackup, rect_ring


def synthetic_board(
    n_nets: int, segs_per_net: int = 5, *, seed: int = 1, pitch: float = 0.4e-3, cell: float = 4e-3
):
    """4-layer board: L1/L4 routing, L2/L3 solid GND with one antipad per via.

    Each net is a Manhattan chain of `segs_per_net` segments alternating between L1 and L4
    (a via at every layer change), so there are n_nets * segs_per_net tracks.
    """
    rng = np.random.default_rng(seed)
    cells = int(np.ceil(np.sqrt(n_nets * 1.5)))
    size = max(20e-3, cells * cell + 4e-3)
    stack = make_stackup([0.2e-3, 0.4e-3, 0.2e-3], roles=["signal", "plane", "plane", "signal"])
    smd = Padstack(
        id="smd",
        drill=None,
        layers=(PadstackLayer(layer="L1", pad=RectShape(width=0.3e-3, height=0.3e-3)),),
    )
    smd4 = Padstack(
        id="smd4",
        drill=None,
        layers=(PadstackLayer(layer="L4", pad=RectShape(width=0.3e-3, height=0.3e-3)),),
    )
    via_ps = Padstack(
        id="via",
        drill=Drill(kind="plated", diameter=0.2e-3),
        layers=(
            PadstackLayer(layer="L1", pad=CircleShape(diameter=0.4e-3)),
            PadstackLayer(layer="L2", pad=None, antipad=CircleShape(diameter=0.7e-3)),
            PadstackLayer(layer="L3", pad=None, antipad=CircleShape(diameter=0.7e-3)),
            PadstackLayer(layer="L4", pad=CircleShape(diameter=0.4e-3)),
        ),
    )
    tracks: list[Track] = []
    pads: list[Pad] = []
    vias: list[Via] = []
    holes: list[list[tuple[float, float]]] = []
    nets = [Net(id="GND", kind="ground")]
    order = rng.permutation(cells * cells)[:n_nets]
    for n, cell_id in enumerate(order):
        net = f"N{n}"
        nets.append(Net(id=net, kind="signal"))
        cx = (cell_id % cells) * cell + 2e-3
        cy = (cell_id // cells) * cell + 2e-3
        x, y = cx, cy
        layer = "L1"
        pads.append(
            Pad(
                id=f"p{n}a",
                component=f"U{n}",
                number="1",
                padstack="smd",
                position=(x, y),
                rotation=0.0,
                net=net,
                kind="smd",
            )
        )
        for k in range(segs_per_net):
            nx, ny = (x + 4 * pitch, y) if k % 2 == 0 else (x, y + 4 * pitch)
            tracks.append(
                Track(
                    id=f"t{n}_{k}",
                    net=net,
                    layer=layer,
                    width=0.1e-3,
                    path=(LineSeg(start=(x, y), end=(nx, ny)),),
                )
            )
            x, y = nx, ny
            if k % 2 == 1 and k < segs_per_net - 1:
                vias.append(
                    Via(
                        id=f"v{n}_{k}",
                        padstack="via",
                        position=(x, y),
                        net=net,
                        kind="through",
                        start_layer="L1",
                        end_layer="L4",
                    )
                )
                holes.append(circle_ring(x, y, 0.35e-3, 12, clockwise=True))
                layer = "L4" if layer == "L1" else "L1"
        pads.append(
            Pad(
                id=f"p{n}b",
                component=f"U{n}",
                number="2",
                padstack="smd" if layer == "L1" else "smd4",
                position=(x, y),
                rotation=0.0,
                net=net,
                kind="smd",
            )
        )
    full = rect_ring(0.0, 0.0, size, size)
    gnd_fill = Polygon(exterior=Points(full), holes=tuple(Points(h) for h in holes))
    outline = Polygon(exterior=Points(full), holes=())

    def zone(layer: str) -> Zone:
        return Zone(
            id=f"zone:GND:{layer}",
            net="GND",
            layers=(layer,),
            outline=outline,
            fills=(ZoneFill(layer=layer, polygons=(gnd_fill,)),),
        )

    return _assemble(
        "synthetic",
        outline=_outline(0.0, 0.0, size, size),
        stackup=stack,
        nets=nets,
        padstacks=[smd, smd4, via_ps],
        pads=pads,
        vias=vias,
        tracks=tracks,
        zones=[zone("L2"), zone("L3")],
    )
