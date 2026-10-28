# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests on hand-written snippets (no KiCad needed)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from oa_ingest_kicad import KiCadIngestError, KiCadReader, sexpr
from oa_ingest_kicad._geom import arc_from_three, fix_fractured, from_shapely
from oa_model import ArcSeg, validate_board

from .snippets import pcb, read_text

NM = 1e-9


# ---------------------------------------------------------------- s-expression
def test_sexpr_basic_and_pts_array() -> None:
    n = sexpr.parse('(a (b "x y" 2) (pts (xy 1 2) (xy 3.5 -4)) (c))')
    assert sexpr.get_str(n, "b") == "x y"
    pts = sexpr.child(n, "pts")
    assert pts is not None and isinstance(pts[1], np.ndarray)
    assert pts[1].tolist() == [[1.0, 2.0], [3.5, -4.0]]


def test_sexpr_pts_with_arc_falls_back_to_nodes() -> None:
    n = sexpr.parse("(pts (xy 0 0) (arc (start 1 0) (mid 2 1) (end 3 0)))")
    assert not isinstance(n[1], np.ndarray)


def test_sexpr_unbalanced() -> None:
    with pytest.raises(sexpr.SExprError):
        sexpr.parse("(a (b)")
    with pytest.raises(sexpr.SExprError):
        sexpr.parse("(a))")


# ---------------------------------------------------------------- geometry
def test_arc_centre_from_three_points_within_1nm() -> None:
    r = 3.7e-3
    cx, cy = 1.234e-3, -5.678e-3
    a0, a1, a2 = 0.3, 1.9, 4.0
    pts = [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in (a0, a1, a2)]
    arc = arc_from_three(*pts)
    assert arc is not None
    assert math.hypot(arc.center[0] - cx, arc.center[1] - cy) < NM
    assert arc.ccw
    cw = arc_from_three(pts[2], pts[1], pts[0])
    assert cw is not None and not cw.ccw


def test_fractured_polygon_recovers_holes() -> None:
    # 10x10 square with two 2x2 holes, both joined to the outline by zero-width bridges
    ring = np.array(
        [
            (0, 0), (10, 0), (10, 10), (0, 10), (0, 7), (1, 7), (1, 5), (3, 5), (3, 7), (1, 7),
            (0, 7), (0, 3), (1, 3), (1, 1), (3, 1), (3, 3), (1, 3), (0, 3),
        ],
        dtype=float,
    )  # fmt: skip
    polys = from_shapely(fix_fractured(ring))
    assert len(polys) == 1
    assert len(polys[0].holes) == 2


# ---------------------------------------------------------------- reader
def test_track_arc_and_units(tmp_path: Path) -> None:
    body = """(segment (start 1 2) (end 5 2) (width 0.2) (layer "F.Cu") (net "A") (uuid "s1"))
    (arc (start 5 2) (mid 6 3) (end 5 4) (width 0.2) (layer "F.Cu") (net "A") (uuid "a1"))"""
    b = read_text(tmp_path, pcb(body))
    seg, arc = b.tracks
    assert seg.path[0].start == (1e-3, -2e-3) and seg.path[0].end == (5e-3, -2e-3)
    assert seg.id == "k:s1" and seg.layer == "L1" and seg.width == pytest.approx(2e-4)
    a = arc.path[0]
    assert isinstance(a, ArcSeg)
    # centre of the circle through (5,2),(6,3),(5,4) is (5,3): model (5e-3,-3e-3)
    assert a.center[0] == pytest.approx(5e-3, abs=NM) and a.center[1] == pytest.approx(
        -3e-3, abs=NM
    )
    assert not a.ccw  # start top, bulge to +x, end bottom: clockwise in the Y-up model
    assert not [d for d in validate_board(b, strict=True) if d.severity == "error"]


def test_pad_rotation_back_side_rotated_footprint(tmp_path: Path) -> None:
    body = """(footprint "L:F" (layer "B.Cu") (uuid "f1") (at 10 20 90)
      (property "Reference" "U1" (at 0 0 0) (layer "B.SilkS"))
      (property "Value" "x" (at 0 0 0) (layer "B.Fab"))
      (pad "1" smd rect (at 1 2 270) (size 1 0.5) (layers "B.Cu" "B.Mask") (net "A") (uuid "p1")))"""
    b = read_text(tmp_path, pcb(body))
    (p,) = b.pads
    # local (1,2) rotated 90 deg CCW (y-down visual) about (10,20) -> (12,19); Y flipped
    assert p.position[0] == pytest.approx(12e-3, abs=NM)
    assert p.position[1] == pytest.approx(-19e-3, abs=NM)
    assert p.rotation == pytest.approx(math.radians(270))  # absolute, not footprint-relative
    assert b.components[0].side == "bottom"
    ps = {s.id: s for s in b.padstacks}[p.padstack]
    assert [lyr.layer for lyr in ps.layers] == ["L4"]


def test_v9_and_v10_net_forms_give_identical_boards(tmp_path: Path) -> None:
    items9 = """(segment (start 0 0) (end 4 0) (width 0.2) (layer "F.Cu") (net 1) (uuid "s1"))
    (via (at 4 0) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 2) (uuid "v1"))"""
    items10 = items9.replace("(net 1)", '(net "A")').replace("(net 2)", '(net "B")')
    b9 = read_text(
        tmp_path, pcb(items9, net_table='(net 0 "") (net 1 "A") (net 2 "B")'), "a.kicad_pcb"
    )
    b10 = read_text(tmp_path, pcb(items10, version=20260206), "b.kicad_pcb")
    d9 = b9.model_dump(exclude={"meta"})
    d10 = b10.model_dump(exclude={"meta"})
    assert d9 == d10
    assert [n.id for n in b9.nets] == ["A", "B"]


def test_stackup_addsublayer_flattening_and_z(tmp_path: Path) -> None:
    b = read_text(tmp_path, pcb(""))
    st = b.stackup
    names = [(lyr.id, lyr.source_name) for lyr in st.layers]
    assert ("D2", "dielectric 2") in names and ("D3", "dielectric 2/2") in names
    assert st.known and st.finish == "ENIG"
    assert st.layers[-1].z_bottom == 0.0
    assert st.total_thickness == pytest.approx(0.035 * 4e-3 + 0.2e-3 * 2 + 0.8e-3, abs=NM)
    d3 = next(lyr for lyr in st.layers if lyr.id == "D3")
    assert d3.thickness == pytest.approx(0.3e-3)


def test_missing_stackup_defaulted(tmp_path: Path) -> None:
    b = read_text(tmp_path, pcb("", stackup=None))
    assert not b.stackup.known
    assert any(d.code == "INGEST.STACKUP_DEFAULTED" for d in b.diagnostics)
    validate_board(b, strict=True)


def test_via_kinds_and_backdrill_end_layer_is_cut(tmp_path: Path) -> None:
    body = """(via (at 1 1) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "A") (uuid "v1")
        (tertiary_drill (size 0.8) (layers "B.Cu" "In1.Cu")))
    (via blind (at 3 1) (size 0.5) (drill 0.2) (layers "F.Cu" "In2.Cu") (net "A") (uuid "v2"))
    (via buried (at 5 1) (size 0.5) (drill 0.2) (layers "In1.Cu" "In2.Cu") (net "A") (uuid "v3"))
    (via micro (at 7 1) (size 0.3) (drill 0.1) (layers "F.Cu" "In1.Cu") (net "A") (uuid "v4"))
    (via (at 9 1) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "A") (uuid "v5")
        (backdrill (size 0.8) (layers "F.Cu" "In1.Cu")))"""
    b = read_text(tmp_path, pcb(body))
    kinds = {v.id: v.kind for v in b.vias}
    assert kinds == {
        "k:v1": "through",
        "k:v2": "blind",
        "k:v3": "buried",
        "k:v4": "micro",
        "k:v5": "through",
    }
    v1 = next(v for v in b.vias if v.id == "k:v1")
    (bd,) = v1.backdrills
    assert (bd.side, bd.last_removed_layer, bd.must_not_cut) == ("bottom", "L2", "L1")
    v5 = next(v for v in b.vias if v.id == "k:v5")
    assert (v5.backdrills[0].side, v5.backdrills[0].last_removed_layer) == ("top", "L2")
    assert v5.backdrills[0].must_not_cut == "L3"
    validate_board(b, strict=True)


def test_per_layer_via_padstack(tmp_path: Path) -> None:
    body = """(via (at 1 1) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "A") (uuid "v1")
        (padstack (mode front_inner_back) (layer "F.Cu" (size 0.9)) (layer "Inner" (size 0)) (layer "B.Cu" (size 0.7))))"""
    b = read_text(tmp_path, pcb(body))
    ps = b.padstacks[0]
    sizes = [getattr(lyr.pad, "diameter", None) for lyr in ps.layers]
    assert sizes == [pytest.approx(9e-4), None, None, pytest.approx(7e-4)]


def test_zone_unfilled_and_filled(tmp_path: Path) -> None:
    unfilled = """(zone (net "A") (layer "F.Cu") (uuid "z1") (fill yes)
      (polygon (pts (xy 1 1) (xy 9 1) (xy 9 9) (xy 1 9))))"""
    filled = """(zone (net "A") (layer "In1.Cu") (uuid "z2") (fill yes)
      (polygon (pts (xy 1 1) (xy 9 1) (xy 9 9) (xy 1 9)))
      (filled_polygon (layer "In1.Cu") (pts (xy 1 1) (xy 9 1) (xy 9 9) (xy 1 9))))"""
    b = read_text(tmp_path, pcb(unfilled + filled))
    z1, z2 = b.zones
    assert z1.fill_state == "unfilled" and z2.fill_state == "filled"
    assert any(d.code == "INGEST.ZONE_UNFILLED" and d.path == "/zones/0" for d in b.diagnostics)
    assert z2.fills[0].polygons[0].exterior.array.shape == (4, 2)


def test_stale_fill_is_flagged(tmp_path: Path) -> None:
    body = """(zone (net "A") (layer "F.Cu") (uuid "z1") (fill yes)
      (polygon (pts (xy 1 1) (xy 9 1) (xy 9 9) (xy 1 9)))
      (filled_polygon (layer "F.Cu") (pts (xy 1 1) (xy 9 1) (xy 9 9) (xy 1 9))))
    (segment (start 2 5) (end 8 5) (width 0.3) (layer "F.Cu") (net "B") (uuid "s1"))"""
    b = read_text(tmp_path, pcb(body))
    assert b.zones[0].fill_state == "stale_suspected"


def test_keepout_zone(tmp_path: Path) -> None:
    body = """(zone (layer "F.Cu") (uuid "k1") (hatch edge 0.5)
      (keepout (tracks not_allowed) (vias allowed) (pads allowed) (copperpour not_allowed) (footprints allowed))
      (polygon (pts (xy 1 1) (xy 3 1) (xy 3 3) (xy 1 3))))"""
    b = read_text(tmp_path, pcb(body))
    z = b.zones[0]
    assert z.net is None and z.keepout is not None
    assert z.keepout.tracks and z.keepout.copper_pour and not z.keepout.vias
    assert z.fill_state == "filled"


def test_outline_chaining_with_hole_and_gap_tolerance(tmp_path: Path) -> None:
    edge = """(gr_line (start 0 0) (end 20 0) (layer "Edge.Cuts") (uuid "1"))
    (gr_arc (start 20 0) (mid 22 5) (end 20 10) (layer "Edge.Cuts") (uuid "2"))
    (gr_line (start 20.0005 10) (end 0 10) (layer "Edge.Cuts") (uuid "3"))
    (gr_line (start 0 10) (end 0 0) (layer "Edge.Cuts") (uuid "4"))
    (gr_circle (center 10 5) (end 12 5) (layer "Edge.Cuts") (uuid "5"))"""
    b = read_text(tmp_path, pcb("", edge=edge))
    o = b.outline
    assert len(o.polygon.holes) == 1
    assert any(isinstance(s, ArcSeg) for s in o.edges)
    area = 0.5 * np.sum(
        o.polygon.exterior.array[:, 0] * np.roll(o.polygon.exterior.array[:, 1], -1)
        - np.roll(o.polygon.exterior.array[:, 0], -1) * o.polygon.exterior.array[:, 1]
    )
    assert area > 20e-3 * 10e-3  # larger than the rectangle: the arc bulges outward
    assert not any(d.code.startswith("INGEST.OUTLINE") for d in b.diagnostics)


def test_open_outline_reported(tmp_path: Path) -> None:
    edge = '(gr_line (start 0 0) (end 20 0) (layer "Edge.Cuts") (uuid "1"))'
    b = read_text(tmp_path, pcb("", edge=edge))
    codes = {d.code for d in b.diagnostics}
    assert {"INGEST.OUTLINE_OPEN", "INGEST.OUTLINE_MISSING"} <= codes


def test_unknown_token_is_info_and_not_fatal(tmp_path: Path) -> None:
    body = '(segment (start 0 0) (end 1 0) (width 0.2) (layer "F.Cu") (frobnicate 3) (uuid "s"))'
    b = read_text(tmp_path, pcb(body))
    d = [x for x in b.diagnostics if x.code == "INGEST.UNKNOWN_TOKEN"]
    assert len(d) == 1 and d[0].severity == "info" and "frobnicate" in d[0].message
    assert len(b.tracks) == 1


def test_custom_pad_is_unioned_and_slot_normalized(tmp_path: Path) -> None:
    body = """(footprint "L:F" (layer "F.Cu") (uuid "f1") (at 0 0)
      (property "Reference" "J1" (at 0 0) (layer "F.SilkS"))
      (pad "1" smd custom (at 5 5) (size 1 1) (layers "F.Cu") (options (clearance outline) (anchor rect))
        (primitives (gr_poly (pts (xy 0 0) (xy 3 0) (xy 3 1) (xy 0 1)) (width 0) (fill yes))))
      (pad "2" thru_hole oval (at 8 5) (size 1 2) (drill oval 0.5 1.5) (layers "*.Cu" "*.Mask") (net "A")))"""
    b = read_text(tmp_path, pcb(body))
    p1, p2 = b.pads
    ps = {s.id: s for s in b.padstacks}
    shape = ps[p1.padstack].layers[0].pad
    assert shape is not None and shape.kind == "polygon" and len(shape.polygons) == 1
    d = ps[p2.padstack].drill
    assert d is not None and d.shape == "slot" and d.slot_length == pytest.approx(1.5e-3)
    assert p2.rotation == pytest.approx(math.pi / 2)  # slot ran along local y: frame rotated
    assert len(ps[p2.padstack].layers) == 4


def test_bad_file_raises_structured_error(tmp_path: Path) -> None:
    p = tmp_path / "x.kicad_pcb"
    p.write_text("(kicad_pcb (version 1)", encoding="utf-8")
    with pytest.raises(KiCadIngestError) as ei:
        KiCadReader().read(p)
    assert ei.value.code == "INGEST_PARSE_ERROR" and ei.value.hint
    assert KiCadReader.can_read(tmp_path / "missing") is False


def test_net_classes_from_project(tmp_path: Path) -> None:
    import json

    pro = {
        "net_settings": {
            "classes": [
                {"name": "Default", "track_width": 0.2, "clearance": 0.1, "priority": 2147483647},
                {"name": "90ohm", "diff_pair_width": 0.14, "diff_pair_gap": 0.15, "priority": 0},
            ],
            "netclass_patterns": [{"netclass": "90ohm", "pattern": "/USB*"}],
        }
    }
    (tmp_path / "t.kicad_pro").write_text(json.dumps(pro), encoding="utf-8")
    body = """(segment (start 0 0) (end 1 0) (width 0.2) (layer "F.Cu") (net "/USB_P") (uuid "a"))
    (segment (start 0 1) (end 1 1) (width 0.2) (layer "F.Cu") (net "/USB_N") (uuid "b"))
    (segment (start 0 2) (end 1 2) (width 0.2) (layer "F.Cu") (net "GND") (uuid "c"))"""
    b = read_text(tmp_path, pcb(body))
    cls = {n.id: n.net_class for n in b.nets}
    assert cls == {"/USB_N": "90ohm", "/USB_P": "90ohm", "GND": "Default"}
    c90 = next(c for c in b.net_classes if c.name == "90ohm")
    assert c90.target_z_diff == 90.0 and c90.patterns == ("/USB*",)
    assert next(n for n in b.nets if n.id == "GND").kind == "ground"
    (dp,) = b.diff_pairs
    assert (dp.p_net, dp.n_net, dp.target_z_diff) == ("/USB_P", "/USB_N", 90.0)
