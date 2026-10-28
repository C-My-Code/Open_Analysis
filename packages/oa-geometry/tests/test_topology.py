# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Net topology, segments, stubs and islands (WP-104 stage 2)."""

from __future__ import annotations

import pytest
from geometry_helpers import poly, rect_ring, track, with_items, zone

from oa_geometry import BoardGeometry, UnknownItem
from oa_model.copper import Zone, ZoneFill
from oa_model.geometry import LineSeg
from oa_testkit.builders import (
    cpwg_board,
    diff_pair_board,
    microstrip_board,
    power_plane_board,
    via_transition_board,
)


def test_microstrip_topology() -> None:
    g = BoardGeometry.build(microstrip_board())
    topo = g.topology("SIG")
    assert {n.id for n in topo.nodes} == {"pad:pad:J1.1", "pad:pad:J2.1"}
    assert topo.root == "pad:pad:J1.1"
    assert [e.kind for e in topo.edges] == ["segment"]
    assert topo.stubs == ()
    (seg,) = g.segments("SIG")
    assert seg.id == "trk:SIG:1"
    assert seg.s_start == 0.0
    assert seg.length == pytest.approx(25e-3)
    nxg = topo.networkx()
    assert nxg.number_of_nodes() == 2 and nxg.number_of_edges() == 1


def test_root_override_reverses_segments() -> None:
    g = BoardGeometry.build(microstrip_board())
    (seg,) = g.segments("SIG", root="pad:J2.1")
    assert isinstance(seg.seg, LineSeg)
    assert seg.seg.start == pytest.approx((25e-3, 0.0))
    assert seg.seg.end == pytest.approx((0.0, 0.0))
    with pytest.raises(UnknownItem):
        g.topology("SIG", root="nope")
    with pytest.raises(UnknownItem):
        g.topology("NOT_A_NET")


def test_via_transition_topology_and_s() -> None:
    b = via_transition_board()
    g = BoardGeometry.build(b)
    topo = g.topology("SIG")
    kinds = sorted(n.kind for n in topo.nodes)
    assert kinds == ["pad", "pad", "via", "via"]
    barrel = next(e for e in topo.edges if e.kind == "via")
    lay = {layer.id: layer for layer in b.stackup.layers}
    z_mid = lambda i: (lay[i].z_bottom + lay[i].z_top) / 2  # noqa: E731
    assert barrel.length == pytest.approx(z_mid("L1") - z_mid("L4"), abs=1e-12)
    segs = g.segments("SIG")
    assert [s.id for s in segs] == ["trk:SIG:L1", "trk:SIG:L4"]
    assert segs[1].s_start == pytest.approx(12.5e-3 + barrel.length, abs=1e-12)
    assert topo.stubs == ()


def test_via_stub_in_topology() -> None:
    b = via_transition_board()
    only_l1 = with_items(b, tracks=[t for t in b.tracks if t.layer == "L1"])
    g = BoardGeometry.build(only_l1)
    topo = g.topology("SIG")
    (stub,) = [s for s in topo.stubs if s.kind == "via_stub"]
    lay = {layer.id: layer for layer in b.stackup.layers}
    assert stub.length == pytest.approx(lay["L1"].z_bottom - lay["L4"].z_bottom, abs=1e-9)
    assert stub.edges == ("barrel:via:SIG:1:0",)
    assert g.via_stub("via:SIG:1", ["L1"]) == pytest.approx(stub.length)


def test_t_junction_splits_and_branch_stub() -> None:
    b = microstrip_board()
    branch = track("br", "SIG", "L1", 0.38e-3, (10e-3, 0.0), (10e-3, 3e-3))
    g = BoardGeometry.build(with_items(b, extra_tracks=[branch]))
    segs = g.segments("SIG")
    assert [s.id for s in segs] == ["trk:SIG:1.0", "trk:SIG:1.1", "br"] or {s.id for s in segs} == {
        "trk:SIG:1.0",
        "trk:SIG:1.1",
        "br",
    }
    by = {s.id: s for s in segs}
    assert by["trk:SIG:1.0"].length == pytest.approx(10e-3)
    assert by["trk:SIG:1.1"].length == pytest.approx(15e-3)
    assert by["br"].s_start == pytest.approx(10e-3)
    assert by["trk:SIG:1.1"].s_start == pytest.approx(10e-3)
    topo = g.topology("SIG")
    assert sum(1 for n in topo.nodes if n.kind == "junction") == 1
    (stub,) = topo.stubs
    assert stub.kind == "branch"
    assert stub.length == pytest.approx(3e-3)


def test_arc_segment_oriented_and_measured() -> None:
    import math

    from geometry_helpers import arc_track

    b = microstrip_board()
    r = 5e-3
    arc = arc_track("arc", "SIG", "L1", 0.2e-3, (0.0 + r, 0.0 + 0.0), (0.0, r), (0.0, 0.0))
    g = BoardGeometry.build(with_items(b, tracks=[arc]))
    (s,) = g.segments("SIG")
    assert s.length == pytest.approx(math.pi / 2 * r)


def test_islands_power_plane() -> None:
    b = power_plane_board()
    g = BoardGeometry.build(b)
    assert len(g.islands("VDD")) == 1
    assert len(g.islands("GND")) == 1
    isl = g.islands("VDD")[0]
    assert set(isl.layers) == {"L1", "L3"}
    # without the via array every pad is isolated from the plane: plane + 6 pads
    no_vias = BoardGeometry.build(with_items(b, vias=[]))
    assert len(no_vias.islands("VDD")) == 1 + 6
    # splitting the VDD plane into two halves, vias connect the pads to their half: 2 islands
    wx, wy = 40e-3, 30e-3
    left = poly(rect_ring(0.0, -wy / 2, 19e-3, wy / 2))
    right = poly(rect_ring(21e-3, -wy / 2, wx, wy / 2))
    old = next(z for z in b.zones if z.net == "VDD")
    split = Zone(
        id=old.id,
        net="VDD",
        layers=("L3",),
        outline=old.outline,
        fills=(ZoneFill(layer="L3", polygons=(left, right)),),
    )
    two = BoardGeometry.build(
        with_items(b, zones=[z for z in b.zones if z.net != "VDD"], extra_zones=[split])
    )
    assert len(two.islands("VDD")) == 2
    assert two.islands("VDD")[0].area >= two.islands("VDD")[1].area


def test_islands_cpwg_fence_connects_ground() -> None:
    b = cpwg_board()
    g = BoardGeometry.build(b)
    assert len(g.islands("GND")) == 1
    assert len(BoardGeometry.build(with_items(b, vias=[])).islands("GND")) == 3


def test_zone_hub_in_topology() -> None:
    b = power_plane_board()
    g = BoardGeometry.build(b)
    topo = g.topology("VDD")
    assert any(n.kind == "zone" for n in topo.nodes)
    assert topo.networkx().number_of_nodes() > 6
    # a zone-only net still gets a root and distances
    assert topo.root is not None


def test_diff_pair_independent_topologies() -> None:
    g = BoardGeometry.build(diff_pair_board())
    assert len(g.segments("DP_P")) == 1
    assert len(g.segments("DP_N")) == 1


def test_unused_zone_helper() -> None:
    # helper sanity: zone() builds a valid zone
    z = zone("z", "GND", "L2", poly(rect_ring(0, 0, 1e-3, 1e-3)))
    assert z.fills[0].polygons[0].exterior.array.shape == (4, 2)
