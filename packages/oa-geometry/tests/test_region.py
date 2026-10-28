# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""cut_region and resolve_terminal (WP-104 stage 5)."""

from __future__ import annotations

import pytest

from oa_geometry import BoardGeometry, RegionError, RegionSpec, TerminalError, UnknownItem
from oa_model.setup.ports import (
    NearestReferenceTerminal,
    PadTerminal,
    PinGroupTerminal,
    PointTerminal,
)
from oa_testkit.builders import microstrip_board, power_plane_board, via_transition_board
from oa_testkit.golden import assert_close_to_golden


def test_cut_region_via_transition() -> None:
    b = via_transition_board()
    g = BoardGeometry.build(b)
    bbox = (5e-3, -4e-3, 20e-3, 4e-3)
    cut = g.cut_region(RegionSpec(nets=("SIG",), bbox=bbox))
    st = cut.structure
    assert cut.bbox == bbox
    by_id = {c.id: c for c in st.conductors}
    assert by_id["L2:GND"].role == "reference" and by_id["L1:SIG"].role == "signal"
    # antipads preserved as holes: one per plane layer, as in the builder
    holes = sum(
        len(p.holes)
        for c in st.conductors
        if c.net == "GND" and c.layer in ("L2", "L3")
        for p in c.polygons
    )
    builder_holes = sum(len(poly.holes) for z in b.zones for f in z.fills for poly in f.polygons)
    assert holes == builder_holes == 2
    # every copper polygon inside the box is present: one body per (layer, net) with copper
    assert {c.id for c in st.conductors} == {
        "L1:SIG",
        "L1:GND",
        "L2:GND",
        "L3:GND",
        "L4:SIG",
        "L4:GND",
    }
    # exactly the two signal traces cross the box
    cr = cut.boundary_crossings
    assert len(cr) == 2
    assert {(c.layer, c.face) for c in cr} == {("L1", "xmin"), ("L4", "xmax")}
    assert all(c.net == "SIG" and c.width == pytest.approx(0.38e-3) for c in cr)
    assert {v.id for v in st.vias} == {f"via:{v.id}" for v in b.vias}
    sig_via = next(v for v in st.vias if v.id == "via:via:SIG:1")
    assert sig_via.role == "signal" and sig_via.r_outer == pytest.approx(0.15e-3)
    assert st.bbox.zmin == pytest.approx(0.0) and st.bbox.zmax == pytest.approx(
        b.stackup.total_thickness
    )
    assert len(st.slabs) >= 3
    assert st.provenance.nets == ("SIG",)
    assert st.key().startswith("st1:")
    assert_close_to_golden(
        {"antipad_holes": holes, "crossings": len(cr), "conductors": len(st.conductors)},
        "geometry",
        "cut_region_via_transition",
    )


def test_cut_region_auto_bbox_layers_and_keep_all() -> None:
    g = BoardGeometry.build(via_transition_board())
    cut = g.cut_region(RegionSpec(nets=("SIG",), margin=1e-3))
    xmin = cut.bbox[0]
    assert xmin == pytest.approx(-0.3e-3 - 1e-3, abs=1e-9)
    assert cut.boundary_crossings == ()
    sub = g.cut_region(RegionSpec(nets=("SIG",), margin=1e-3, layers=("L1", "L2")))
    assert {c.layer for c in sub.structure.conductors} == {"L1", "L2"}
    assert sub.structure.bbox.zmin == pytest.approx(g.conductor("L2").z_bottom)
    only = g.cut_region(
        RegionSpec(nets=("SIG",), margin=1e-3, keep_all_copper=False, reference_nets=("GND",))
    )
    assert {c.net for c in only.structure.conductors} <= {"SIG", "GND"}
    wide = g.cut_region(RegionSpec(nets=("SIG",), f_max=10e9))
    assert wide.bbox[2] - wide.bbox[0] > 25e-3 + 2 * 0.1e-3


def test_cut_region_errors() -> None:
    g = BoardGeometry.build(microstrip_board())
    with pytest.raises(RegionError):
        g.cut_region(RegionSpec(nets=()))
    with pytest.raises(UnknownItem):
        g.cut_region(RegionSpec(nets=("NOPE",)))
    with pytest.raises(RegionError):
        g.cut_region(RegionSpec(nets=("SIG",), bbox=(1.0, 0.0, 0.0, 1.0)))


def test_resolve_terminals() -> None:
    b = power_plane_board()
    g = BoardGeometry.build(b)
    r = g.resolve_terminal(PadTerminal(component="U1", pin="1"))
    assert r.net == "VDD" and r.layer == "L1" and r.pads == ("pad:U1.1",)
    assert r.extent == pytest.approx((0.8e-3, 0.8e-3))
    assert r.to_em().layer == "L1"
    grp = g.resolve_terminal(PinGroupTerminal(component="U1", nets=("VDD", "GND")))
    assert len(grp.pads) == 2 and grp.extent[1] == pytest.approx(1.5e-3 + 0.8e-3)
    pt = g.resolve_terminal(PointTerminal(position=r.position, layer="L1", net="VDD"))
    assert pt.net == "VDD"
    with pytest.raises(TerminalError):
        g.resolve_terminal(PointTerminal(position=r.position, layer="L1", net="GND"))
    with pytest.raises(TerminalError):
        g.resolve_terminal(PointTerminal(position=(1.0, 1.0), layer="L1"))
    with pytest.raises(TerminalError):
        g.resolve_terminal(PadTerminal(pad="nope"))
    ref = g.resolve_terminal(NearestReferenceTerminal(), near=r.position, layer="L1")
    assert ref.net == "GND" and ref.kind == "nearest_reference"
    with pytest.raises(TerminalError):
        g.resolve_terminal(NearestReferenceTerminal())
