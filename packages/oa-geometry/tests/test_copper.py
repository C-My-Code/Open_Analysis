# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Copper unions, voids, indexes, drills, stubs (WP-104 stage 1)."""

from __future__ import annotations

import itertools
import math

import pytest
import shapely
from geometry_helpers import arc_track, track, with_items
from hypothesis import given, settings
from hypothesis import strategies as st

from oa_geometry import BoardGeometry, ItemRef, UnknownItem, via_stub
from oa_geometry._shapes import arc_points, shape_geometry
from oa_model.geometry import ArcSeg
from oa_model.padstack import (
    ChamferedRectShape,
    CircleShape,
    ObroundShape,
    RectShape,
    RoundRectShape,
    TrapezoidShape,
)
from oa_model.vias import Backdrill
from oa_testkit.builders import (
    cpwg_board,
    microstrip_board,
    power_plane_board,
    via_transition_board,
)


def test_microstrip_copper_by_net() -> None:
    b = microstrip_board()
    g = BoardGeometry.build(b)
    assert BoardGeometry.build(b) is g
    nets = g.copper_by_net("L1")
    assert set(nets) == {"SIG"}
    sig = g.copper("L1", "SIG")
    assert len(sig.geoms) == 1
    expected = shapely.union_all(
        [
            shapely.LineString([(0, 0), (25e-3, 0)]).buffer(0.19e-3, quad_segs=256),
            shapely.box(-0.3e-3, -0.3e-3, 0.3e-3, 0.3e-3),
            shapely.box(25e-3 - 0.3e-3, -0.3e-3, 25e-3 + 0.3e-3, 0.3e-3),
        ]
    )
    assert sig.area == pytest.approx(expected.area, rel=1e-4)
    assert sig.symmetric_difference(expected).area < 1e-3 * expected.area
    assert g.copper("L2", "GND").area == pytest.approx(35e-3 * 10e-3, rel=1e-12)
    assert g.copper("L1").area == pytest.approx(sig.area)
    assert g.copper("L1", "NOPE").is_empty


def test_unknown_layer() -> None:
    g = BoardGeometry.build(microstrip_board())
    with pytest.raises(UnknownItem):
        g.copper("L9")
    with pytest.raises(UnknownItem):
        g.copper("D1")


def test_voids_and_antipads_via_transition() -> None:
    b = via_transition_board()
    g = BoardGeometry.build(b)
    v = g.voids("L2", "GND")
    # the outline minus GND copper is exactly the antipad (the 32-gon of the builder)
    assert len(v.geoms) == 1
    ap = math.pi * (0.5e-3) ** 2 * (math.sin(2 * math.pi / 32) * 32 / (2 * math.pi))
    assert v.area == pytest.approx(ap, rel=1e-9)
    assert g.voids("L2", "SIG").area == pytest.approx(g.outline().area)
    plane = g.copper("L2", "GND")
    assert len(plane.geoms) == 1
    assert len(plane.geoms[0].interiors) == 1  # the antipad


def test_arc_tolerance_bounded() -> None:
    seg = ArcSeg(start=(1e-3, 0), end=(0, 1e-3), center=(0, 0), ccw=True)
    for tol in (1e-4, 1e-6, 1e-8):
        pts = arc_points(seg, tol)
        mids = (pts[:-1] + pts[1:]) / 2
        sag = 1e-3 - (mids**2).sum(axis=1) ** 0.5
        assert sag.max() <= tol * 1.0001
        assert tuple(pts[0]) == (1e-3, 0) and tuple(pts[-1]) == (0, 1e-3)
    # full circle (start == end) sweeps 2*pi
    full = ArcSeg(start=(1e-3, 0), end=(1e-3, 0), center=(0, 0), ccw=True)
    ring = shapely.Polygon(arc_points(full, 1e-6))
    assert ring.area == pytest.approx(math.pi * 1e-6, rel=5e-3)


def test_arc_track_buffer_area() -> None:
    b = microstrip_board()
    arc = arc_track("arc1", "SIG", "L1", 0.2e-3, (5e-3, 0), (0, 5e-3), (0, 0))
    g = BoardGeometry.build(with_items(b, extra_tracks=[arc]))
    ids = {r.id for r in g.strtree("L1").query(shapely.Point(0, 5e-3).buffer(1e-4))}
    assert "arc1" in ids
    part = shapely.union_all(
        [
            g.strtree("L1").geometry(int(i))
            for i in g.strtree("L1").query_indices(shapely.box(-1e-3, 0.5e-3, 6e-3, 6e-3))
        ]
    )
    area = (math.pi / 2) * 5e-3 * 0.2e-3 + math.pi * (0.1e-3) ** 2
    assert part.area >= area * 0.99  # arc strip (+ overlap with the straight track)


def test_pad_shapes_rotated_area() -> None:
    tol = 1e-6
    assert shape_geometry(RectShape(width=2e-3, height=1e-3), tol).area == pytest.approx(2e-6)
    rr = shape_geometry(RoundRectShape(width=2e-3, height=1e-3, corner_radius=0.25e-3), tol)
    assert rr.area == pytest.approx(2e-6 - (4 - math.pi) * 0.25e-3**2, rel=5e-3)
    ob = shape_geometry(ObroundShape(width=2e-3, height=1e-3), tol)
    assert ob.area == pytest.approx(1e-3 * 1e-3 + math.pi * 0.5e-3**2, rel=5e-3)
    ci = shape_geometry(CircleShape(diameter=1e-3), tol)
    assert ci.area == pytest.approx(math.pi * 0.25e-6, rel=5e-3)
    ch = shape_geometry(
        ChamferedRectShape(width=2e-3, height=1e-3, chamfer=0.2e-3, corners=("tl", "br")), tol
    )
    assert ch.area == pytest.approx(2e-6 - 2 * 0.5 * 0.2e-3**2, rel=1e-9)
    tr = shape_geometry(TrapezoidShape(width=2e-3, height=1e-3, delta_x=0.0, delta_y=0.2e-3), tol)
    assert tr.is_valid and tr.area > 0


def test_cpwg_islands_ground_is_connected_by_fence() -> None:
    g = BoardGeometry.build(cpwg_board())
    # L1 GND is two separate pours, joined only through the via fence to L2
    assert len(g.copper("L1", "GND").geoms) == 2


def test_strtree_query_and_nearest() -> None:
    b = power_plane_board()
    g = BoardGeometry.build(b)
    tree = g.strtree("L1")
    refs = tree.query(shapely.Point(b.pads[0].position).buffer(1e-5))
    assert ItemRef("pad", b.pads[0].id) in refs
    assert ItemRef("via", "via:VDD:U1") in refs or ItemRef("via", "via:GND:U1") in refs
    assert tree.nearest(shapely.Point(1.0, 1.0), max_distance=1e-3) is None
    assert tree.nearest(shapely.Point(b.pads[0].position)) is not None


def test_drills() -> None:
    b = via_transition_board()
    d = BoardGeometry.build(b).drills()
    assert len(d) == len(b.vias) == 5
    assert d[0].diameter == pytest.approx(0.3e-3)
    assert {x.owner.kind for x in d} == {"via"}


def test_via_stub_through_and_backdrilled() -> None:
    b = via_transition_board()
    g = BoardGeometry.build(b)
    sig = next(v for v in b.vias if v.id == "via:SIG:1")
    lay = {layer.id: layer for layer in b.stackup.layers}
    # signal used on L1 only: stub runs from the bottom of L1 to the barrel end (bottom of L4)
    assert g.via_stub(sig, ["L1"]) == pytest.approx(
        lay["L1"].z_bottom - lay["L4"].z_bottom, abs=1e-9
    )
    # used on L1 and L4: no stub
    assert via_stub(b, sig, ["L1", "L4"]) == pytest.approx(0.0, abs=1e-12)
    # backdrill from the bottom through L4: barrel now ends at the top of L4
    bd = sig.model_copy(
        update={"backdrills": (Backdrill(side="bottom", diameter=0.5e-3, last_removed_layer="L4"),)}
    )
    assert via_stub(b, bd, ["L1"]) == pytest.approx(lay["L1"].z_bottom - lay["L4"].z_top, abs=1e-9)
    # explicit depth wins
    bd2 = sig.model_copy(
        update={
            "backdrills": (
                Backdrill(side="bottom", diameter=0.5e-3, last_removed_layer="L4", depth=0.1e-3),
            )
        }
    )
    assert via_stub(b, bd2, ["L1"]) == pytest.approx(lay["L1"].z_bottom - 0.1e-3, abs=1e-9)
    # backdrilled layers lose their via pad
    gb = BoardGeometry.build(with_items(b, vias=[bd, *b.vias[1:]]))
    assert gb.copper("L4", "SIG").area < g.copper("L4", "SIG").area


@settings(max_examples=40, deadline=None)
@given(
    st.lists(st.floats(min_value=-4.0, max_value=4.0), min_size=1, max_size=12, unique=True),
    st.floats(min_value=0.05, max_value=0.4),
)
def test_union_area_equals_sum_of_disjoint_primitives(ys: list[float], w_mm: float) -> None:
    """Parallel tracks at least 1 mm apart are disjoint: union area == sum of primitive areas;
    the union is idempotent."""
    ys_sorted = sorted(ys)
    gaps_ok = all(b - a >= 1.0 for a, b in itertools.pairwise(ys_sorted))
    if not gaps_ok:
        return
    b = microstrip_board()
    tracks = [
        track(
            f"h{i}", "SIG", "L1", w_mm * 1e-3, (1e-3, y * 1e-3 + 20e-3), (20e-3, y * 1e-3 + 20e-3)
        )
        for i, y in enumerate(ys_sorted)
    ]
    g = BoardGeometry.build(with_items(b, tracks=tracks))
    tree = g.strtree("L1")
    total = sum(tree.geometry(i).area for i in range(len(tree)))
    u = g.copper("L1")
    assert u.area == pytest.approx(total, rel=1e-9)
    assert shapely.union_all([u, u]).area == pytest.approx(u.area, rel=1e-12)
    assert shapely.union_all([u]).equals(u)
