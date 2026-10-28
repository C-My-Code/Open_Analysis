# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""ReferenceMap (WP-104 stage 3)."""

from __future__ import annotations

import math

import pytest
from geometry_helpers import arc_track, poly, rect_ring, with_items, zone

from oa_geometry import BoardGeometry
from oa_model.board import Component
from oa_model.nets import Net
from oa_model.pads import Pad
from oa_testkit.builders import (
    microstrip_board,
    split_plane_board,
    stripline_board,
    via_transition_board,
)
from oa_testkit.golden import assert_close_to_golden

NM = 1e-9


def test_microstrip_single_span() -> None:
    b = microstrip_board(h=0.2e-3)
    m = BoardGeometry.build(b).reference_map()
    assert [sid for sid, _ in m.spans] == ["trk:SIG:1"]
    (sp,) = m.for_segment("trk:SIG:1")
    assert sp.s_start == 0.0 and sp.s_end == pytest.approx(25e-3, abs=NM)
    assert sp.above is None
    assert sp.below is not None
    assert sp.below.layer == "L2" and sp.below.net == "GND"
    assert sp.below.distance == pytest.approx(0.2e-3, abs=NM)
    assert sp.below.coverage == 1.0
    assert m.for_segment("nope") == ()
    assert m.k_buffer == 3.0


def test_stripline_both_sides_exact() -> None:
    m = BoardGeometry.build(stripline_board(h1=0.2e-3, h2=0.3e-3)).reference_map()
    (sp,) = m.for_segment("trk:SIG:1")
    assert sp.above is not None and sp.below is not None
    assert sp.above.distance == pytest.approx(0.2e-3, abs=NM)
    assert sp.below.distance == pytest.approx(0.3e-3, abs=NM)
    assert (sp.above.layer, sp.below.layer) == ("L1", "L3")


def test_split_plane_coverage_over_slot() -> None:
    b = split_plane_board()
    m = BoardGeometry.build(b).reference_map()
    spans = m.for_segment("trk:SIG:1")
    partial = [s for s in spans if s.below is None or s.below.coverage < 1.0]
    assert len(partial) == 1
    slot = partial[0]
    assert slot.below is None  # nothing under the trace inside the slot
    left, right = spans[0], spans[-1]
    assert left.below is not None and left.below.net == "GND"
    assert right.below is not None and right.below.net == "VDD"
    assert left.below.coverage == 1.0 and right.below.coverage == 1.0
    actual = {
        "slot_start": slot.s_start,
        "slot_end": slot.s_end,
        "n_spans": len(spans),
        "left_net": left.below.net,
        "right_net": right.below.net,
        "left_distance": left.below.distance,
    }
    assert_close_to_golden(actual, "geometry", "refmap_split_plane")


def test_half_covered_trace_reports_edge() -> None:
    b = microstrip_board()
    half = poly(rect_ring(-5e-3, -5e-3, 30e-3, 0.0))
    nz = zone("zone:GND:L2", "GND", "L2", half)
    m = BoardGeometry.build(with_items(b, zones=[nz])).reference_map()
    (sp,) = m.for_segment("trk:SIG:1")
    assert sp.below is not None
    assert sp.below.coverage == pytest.approx(0.5, abs=1e-6)
    assert sp.below.edge_distance == pytest.approx(0.0, abs=1e-12)


def test_no_reference_when_plane_missing() -> None:
    b = microstrip_board()
    m = BoardGeometry.build(with_items(b, zones=[])).reference_map()
    (sp,) = m.for_segment("trk:SIG:1")
    assert sp.above is None and sp.below is None


def test_own_net_copper_is_not_a_reference() -> None:
    b = microstrip_board()
    own = zone("zone:SIG:L2", "SIG", "L2", poly(rect_ring(-5e-3, -5e-3, 30e-3, 5e-3)))
    m = BoardGeometry.build(with_items(b, zones=[own])).reference_map()
    (sp,) = m.for_segment("trk:SIG:1")
    assert sp.below is None


def test_arc_track_spans_cover_arc_length() -> None:
    b = microstrip_board()
    arc = arc_track("arc", "SIG", "L1", 0.3e-3, (5e-3, -4e-3), (0.0, 1e-3), (0.0, -4e-3))
    g = BoardGeometry.build(with_items(b, extra_tracks=[arc]))
    spans = g.reference_map().for_segment("arc")
    assert spans[0].s_start == 0.0
    assert spans[-1].s_end == pytest.approx(math.pi / 2 * 5e-3, rel=1e-9)
    assert all(s.below is not None and s.below.net == "GND" for s in spans)


def test_via_transition_return_vias() -> None:
    b = via_transition_board()
    m = BoardGeometry.build(b).reference_map()
    (vt,) = m.via_transitions
    assert vt.via == "via:SIG:1" and vt.net == "SIG"
    assert (vt.from_layer, vt.to_layer) == ("L1", "L4")
    assert vt.from_ref is not None and vt.from_ref.layer == "L2"
    assert vt.to_ref is not None and vt.to_ref.layer == "L3"
    assert not vt.ref_net_changes
    assert len(vt.return_vias) == 4
    assert all(r.distance == pytest.approx(1.2e-3, abs=1e-9) for r in vt.return_vias)
    assert vt.stitching_caps == ()


def test_via_transition_reference_change_and_stitch_cap() -> None:
    b = via_transition_board()
    l3 = next(z for z in b.zones if z.id == "zone:GND:L3")
    vdd = l3.model_copy(update={"id": "zone:VDD:L3", "net": "VDD"})
    caps_pads = [
        Pad(
            id=f"pad:C1.{n}",
            component="C1",
            number=n,
            padstack="smd_L1",
            position=(12.5e-3 + dx, 3e-3),
            rotation=0.0,
            net=net,
            kind="smd",
        )
        for n, dx, net in (("1", -0.5e-3, "GND"), ("2", 0.5e-3, "VDD"))
    ]
    comp = Component(
        refdes="C1",
        side="top",
        position=(12.5e-3, 3e-3),
        rotation=0.0,
        pads=tuple(p.id for p in caps_pads),
    )
    # VDD pour must not be ground-kind for the via to see a power reference
    nb = with_items(
        b,
        zones=[z for z in b.zones if z.id != "zone:GND:L3"],
        extra_zones=[vdd],
        extra_nets=[Net(id="VDD", kind="power")],
        pads=[*b.pads, *caps_pads],
    ).model_copy(update={"components": (*b.components, comp)})
    (vt,) = BoardGeometry.build(nb).reference_map().via_transitions
    assert vt.ref_net_changes
    assert {vt.from_ref.net, vt.to_ref.net} == {"GND", "VDD"}  # type: ignore[union-attr]
    assert [c.refdes for c in vt.stitching_caps] == ["C1"]
    assert vt.stitching_caps[0].distance == pytest.approx(3e-3, abs=1e-9)
    assert {r.net for r in vt.return_vias} == {"GND"}


def test_on_demand_spans_match_bulk() -> None:
    from oa_geometry.refmap import piece_spans

    g = BoardGeometry.build(split_plane_board())
    on_demand = piece_spans(g, "trk:SIG:1")
    g2 = BoardGeometry.build(split_plane_board())
    assert g2.reference_map().for_segment("trk:SIG:1") == on_demand
