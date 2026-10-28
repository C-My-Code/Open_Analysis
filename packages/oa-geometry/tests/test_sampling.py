# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""cross_section_at / sample_net (WP-104 stage 4)."""

from __future__ import annotations

import pytest
from geometry_helpers import poly, rect_ring, track, with_items, zone

from oa_geometry import BoardGeometry
from oa_model.stackup import Etch
from oa_model.xsection import CrossSection, XsPlane, XsRegion, XsSlab, XsTrapezoid
from oa_testkit.builders import (
    cpwg_board,
    diff_pair_board,
    microstrip_board,
    split_plane_board,
    stripline_board,
    via_transition_board,
)
from oa_testkit.golden import assert_close_to_golden

NM = 1e-9


def _signal_traps(xs: CrossSection) -> list[XsTrapezoid]:
    return [
        c.shape for c in xs.conductors if c.role == "signal" and isinstance(c.shape, XsTrapezoid)
    ]


def _planes(xs: CrossSection) -> list[XsPlane]:
    return [c.shape for c in xs.conductors if isinstance(c.shape, XsPlane)]


def test_microstrip_section_and_key_constant() -> None:
    b = microstrip_board(width=0.35e-3, h=0.2e-3)
    g = BoardGeometry.build(b)
    (seg,) = g.segments("SIG")
    xs = g.cross_section_at(seg, 10e-3)
    assert xs.topology_hint == "microstrip"
    (sig,) = _signal_traps(xs)
    (plane,) = _planes(xs)
    t = 35e-6
    assert sig.w_bottom == pytest.approx(0.35e-3, abs=NM)
    assert sig.w_top == pytest.approx(0.35e-3, abs=NM)
    assert sig.thickness == pytest.approx(t, abs=NM)
    assert sig.z_bottom - (plane.z_bottom + plane.thickness) == pytest.approx(0.2e-3, abs=NM)
    assert plane.u_min is None and plane.u_max is None
    assert xs.symmetric_about_u0
    keys = {g.cross_section_at(seg, s).key() for s in (2e-3, 5e-3, 12.5e-3, 20e-3, 23e-3)}
    assert len(keys) == 1
    assert xs.provenance is not None
    assert xs.provenance.net_ids == ("SIG",) and xs.provenance.layer == "L1"
    actual = {
        "w_bottom": sig.w_bottom,
        "thickness": sig.thickness,
        "height_over_plane": sig.z_bottom - (plane.z_bottom + plane.thickness),
        "hint": xs.topology_hint,
    }
    assert_close_to_golden(actual, "geometry", "xsection_microstrip")


def test_stripline_symmetric_and_offset() -> None:
    sym = BoardGeometry.build(stripline_board(h1=0.2e-3, h2=0.2e-3))
    xs = sym.cross_section_at(sym.segments("SIG")[0], 5e-3)
    assert xs.topology_hint == "stripline"
    pl = sorted(_planes(xs), key=lambda p: p.z_bottom)
    (sig,) = _signal_traps(xs)
    assert sig.z_bottom - (pl[0].z_bottom + pl[0].thickness) == pytest.approx(0.2e-3, abs=NM)
    assert pl[1].z_bottom - (sig.z_bottom + sig.thickness) == pytest.approx(0.2e-3, abs=NM)
    off = BoardGeometry.build(stripline_board(h1=0.2e-3, h2=0.3e-3))
    xo = off.cross_section_at(off.segments("SIG")[0], 5e-3)
    assert xo.topology_hint == "offset_stripline"
    po = sorted(_planes(xo), key=lambda p: p.z_bottom)
    (so,) = _signal_traps(xo)
    assert so.z_bottom - (po[0].z_bottom + po[0].thickness) == pytest.approx(0.3e-3, abs=NM)
    assert po[1].z_bottom - (so.z_bottom + so.thickness) == pytest.approx(0.2e-3, abs=NM)
    actual = {
        "h_sym_below": sig.z_bottom - (pl[0].z_bottom + pl[0].thickness),
        "h_sym_above": pl[1].z_bottom - (sig.z_bottom + sig.thickness),
        "h_off_below": so.z_bottom - (po[0].z_bottom + po[0].thickness),
        "h_off_above": po[1].z_bottom - (so.z_bottom + so.thickness),
    }
    assert_close_to_golden(actual, "geometry", "xsection_stripline")


def test_cpwg_coplanar_grounds_with_gap() -> None:
    gap, w = 0.15e-3, 0.3e-3
    g = BoardGeometry.build(cpwg_board(width=w, gap=gap))
    (seg,) = g.segments("SIG")
    xs = g.cross_section_at(seg, 12.5e-3)
    assert xs.topology_hint == "cpwg"
    grounds = [c for c in xs.conductors if c.role == "ground" and isinstance(c.shape, XsTrapezoid)]
    assert len(grounds) == 2
    (sig,) = _signal_traps(xs)
    left, right = sorted((c.shape for c in grounds), key=lambda t: t.u_center)  # type: ignore[union-attr]
    assert sig.u_center - w / 2 - (left.u_center + left.w_bottom / 2) == pytest.approx(gap, abs=NM)
    assert (right.u_center - right.w_bottom / 2) - (sig.u_center + w / 2) == pytest.approx(
        gap, abs=NM
    )
    assert len(_planes(xs)) == 1
    assert_close_to_golden({"gap_left": gap, "hint": xs.topology_hint}, "geometry", "xsection_cpwg")


def test_diff_pair_two_signals_p_then_n() -> None:
    g = BoardGeometry.build(diff_pair_board(width=0.15e-3, gap=0.15e-3))
    for net in ("DP_P", "DP_N"):
        (seg,) = g.segments(net)
        xs = g.cross_section_at(seg, 10e-3)
        sigs = [c for c in xs.conductors if c.role == "signal"]
        assert [c.net for c in sigs] == ["DP_P", "DP_N"]
        assert xs.signal_ids() == ("s1", "s2")
        p, n = (c.shape for c in sigs)
        assert isinstance(p, XsTrapezoid) and isinstance(n, XsTrapezoid)
        assert p.u_center != n.u_center
        assert abs(p.u_center - n.u_center) == pytest.approx(0.3e-3, abs=NM)
        assert xs.topology_hint == "edge_coupled_microstrip"
        assert xs.provenance is not None and xs.provenance.net_ids == ("DP_P", "DP_N")
    # same canonical section from either segment's point of view
    kp = g.cross_section_at(g.segments("DP_P")[0], 10e-3).key()
    kn = g.cross_section_at(g.segments("DP_N")[0], 10e-3).key()
    assert kp == kn


def test_aggressor_added_as_signal_and_can_be_dropped() -> None:
    b = microstrip_board()
    agg = track("agg", "SIG2", "L1", 0.38e-3, (1e-3, 1.0e-3), (24e-3, 1.0e-3))
    from oa_model.nets import Net

    nb = with_items(b, extra_tracks=[agg], extra_nets=[Net(id="SIG2", kind="signal")])
    g = BoardGeometry.build(nb)
    (seg,) = g.segments("SIG")
    with_agg = g.cross_section_at(seg, 12e-3, window=1.5e-3)
    assert len(with_agg.signal_ids()) == 2
    assert with_agg.provenance is not None and with_agg.provenance.net_ids == ("SIG", "SIG2")
    without = g.cross_section_at(seg, 12e-3, window=1.5e-3, include_aggressors=False)
    assert len(without.signal_ids()) == 1
    # out of the window: not included
    far = g.cross_section_at(seg, 12e-3, window=0.5e-3)
    assert len(far.signal_ids()) == 1


def test_plane_truncated_to_copper_extent() -> None:
    b = microstrip_board()
    narrow = poly(rect_ring(-5e-3, -0.8e-3, 30e-3, 5e-3))  # plane edge 0.8 mm below the trace
    g = BoardGeometry.build(with_items(b, zones=[zone("zone:GND:L2", "GND", "L2", narrow)]))
    (seg,) = g.segments("SIG")
    xs = g.cross_section_at(seg, 10e-3, window=2e-3)
    (plane,) = _planes(xs)
    (sig,) = _signal_traps(xs)
    # u runs along the left normal (+y): the plane edge is 0.8 mm to the right (-u) of the trace
    assert plane.u_min == pytest.approx(sig.u_center - 0.8e-3, abs=NM)
    assert plane.u_max is None  # copper continues past the window


def test_etch_trapezoid_and_conductivity_temperature() -> None:
    b = microstrip_board(width=0.3e-3)
    layers = tuple(
        layer.model_copy(update={"etch": Etch(kind="top_width_delta", value=0.04e-3)})
        if layer.id == "L1"
        else layer
        for layer in b.stackup.layers
    )
    nb = b.model_copy(update={"stackup": b.stackup.model_copy(update={"layers": layers})})
    g = BoardGeometry.build(nb)
    (seg,) = g.segments("SIG")
    xs = g.cross_section_at(seg, 10e-3)
    sig = _signal_traps(xs)[0]
    assert sig.w_bottom == pytest.approx(0.3e-3, abs=NM)
    assert sig.w_top == pytest.approx(0.26e-3, abs=NM)
    cold = g.cross_section_at(seg, 10e-3).conductors[0].conductivity
    hot = g.cross_section_at(seg, 10e-3, physics_temp=393.15).conductors[0].conductivity
    assert hot < cold == pytest.approx(5.8e7)


def test_solder_mask_slab_and_conformal_region() -> None:
    from oa_model.materials import ConstantDkDf, DielectricMaterial
    from oa_model.stackup import MaskLayer

    b = microstrip_board()
    top = b.stackup.layers[0]
    mask = MaskLayer(
        id="MT",
        thickness=20e-6,
        thickness_over_copper=10e-6,
        z_bottom=top.z_bottom,
        z_top=top.z_bottom + 20e-6,
        material="mask",
    )
    st = b.stackup.model_copy(
        update={
            "layers": (mask, *b.stackup.layers),
            "materials": (
                *b.stackup.materials,
                DielectricMaterial(name="mask", model=ConstantDkDf(dk=3.3, df=0.03, f_ref=1e9)),
            ),
        }
    )
    g = BoardGeometry.build(b.model_copy(update={"stackup": st}))
    (seg,) = g.segments("SIG")
    xs = g.cross_section_at(seg, 10e-3)
    regions = [d for d in xs.dielectrics if isinstance(d.region, XsRegion)]
    assert len(regions) == 1 and regions[0].priority == 10
    ext = regions[0].region.polygon.exterior.array  # type: ignore[union-attr]
    width = ext[:, 0].max() - ext[:, 0].min()
    assert width == pytest.approx(0.38e-3 + 2 * 10e-6, abs=2e-8)
    slabs = [d for d in xs.dielectrics if isinstance(d.region, XsSlab)]
    assert any(abs((d.region.z_top - d.region.z_bottom) - 20e-6) < NM for d in slabs)  # type: ignore[union-attr]


def test_sample_net_events_split_plane() -> None:
    g = BoardGeometry.build(split_plane_board())
    samples = g.sample_net("SIG")
    assert samples[0].s == 0.0
    s_vals = [x.s for x in samples]
    assert s_vals == sorted(s_vals)
    events = {x.event for x in samples}
    assert "no_reference" in events
    assert "ref_change" in events
    gap = [x for x in samples if x.event == "no_reference"]
    assert all(12.2e-3 < x.s < 12.8e-3 for x in gap)


def test_sample_net_width_neckdown_and_layer_change() -> None:
    b = microstrip_board()
    # SIG: 0..10 mm @0.38, 10..15 mm @0.15 (neckdown), 15..25 mm @0.38 (width_change back)
    tr = [
        track("a", "SIG", "L1", 0.38e-3, (0.0, 0.0), (10e-3, 0.0)),
        track("b", "SIG", "L1", 0.15e-3, (10e-3, 0.0), (15e-3, 0.0)),
        track("c", "SIG", "L1", 0.38e-3, (15e-3, 0.0), (25e-3, 0.0)),
    ]
    g = BoardGeometry.build(with_items(b, tracks=tr))
    ev = {x.seg_id: x.event for x in g.sample_net("SIG") if x.event != "regular"}
    assert ev == {"b": "neckdown", "c": "width_change"}
    v = via_transition_board()
    gv = BoardGeometry.build(v)
    evs = [x.event for x in gv.sample_net("SIG")]
    assert "layer_change" in evs


def test_sample_net_diff_pair() -> None:
    g = BoardGeometry.build(diff_pair_board())
    samples = g.sample_net(("DP_P", "DP_N"), ds=5e-3)
    assert len(samples) >= 5
    assert all(len(x.xs.signal_ids()) == 2 for x in samples)
    assert len({x.xs.key() for x in samples}) == 1
