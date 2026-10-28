# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""A synthetic board with many tracks for the performance test."""

from __future__ import annotations

from oa_model.board import Board
from oa_model.nets import Net
from oa_testkit import builders as tb


def many_tracks_board(n_nets: int = 1000, per_net: int = 10, seg_len: float = 5e-3) -> Board:
    """``n_nets`` signal nets, each a chain of ``per_net`` collinear L1 segments (so
    ``n_nets * per_net`` tracks), 0.6 mm pitch, over a solid L2 GND zone."""
    pitch = 0.6e-3
    width = 0.15e-3
    tracks = []
    nets = [Net(id="GND", kind="ground")]
    for i in range(n_nets):
        name = f"N{i:04d}"
        nets.append(Net(id=name, kind="signal"))
        y = i * pitch
        for k in range(per_net):
            tracks.append(
                tb._track(
                    f"trk:{name}:{k}",
                    name,
                    "L1",
                    width,
                    (k * seg_len, y),
                    ((k + 1) * seg_len, y),
                )
            )
    x1 = per_net * seg_len
    y1 = n_nets * pitch
    margin = 5e-3
    ring = tb._poly(tb.rect_ring(-margin, -margin, x1 + margin, y1 + margin))
    stack = tb.make_stackup([0.2e-3], roles=["signal", "plane"])
    return tb._assemble(
        "oa_erc_many_tracks",
        outline=tb._outline(-margin, -margin, x1 + margin, y1 + margin),
        stackup=stack,
        nets=nets,
        tracks=tracks,
        zones=[tb._fill_zone("zone:GND:L2", "GND", "L2", ring)],
    )
