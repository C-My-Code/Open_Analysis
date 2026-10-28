# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reader tests that do not need KiCad: a hand-written rev B file and the WP-102 boards."""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import pytest
import yaml
from board_stats import board_stats
from conftest import DATA, SYNTHETIC

from oa_ingest_ipc2581 import Ipc2581Reader
from oa_model import validate_board
from oa_model.errors import IngestError
from oa_model.setup import ReadOptions

MINI = DATA / "mini_revb.xml"


def test_can_read_sniffs_root_element(tmp_path: Path) -> None:
    assert Ipc2581Reader.can_read(MINI)
    other = tmp_path / "x.xml"
    other.write_text("<?xml version='1.0'?><svg/>")
    assert not Ipc2581Reader.can_read(other)
    assert not Ipc2581Reader.can_read(tmp_path / "missing.xml")


def test_malformed_xml_is_an_ingest_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.xml"
    bad.write_text('<IPC-2581 revision="C"><Ecad>')
    with pytest.raises(IngestError) as ei:
        Ipc2581Reader().read(bad, ReadOptions())
    assert ei.value.code == "INGEST.IPC2581_XML"


def test_mini_rev_b_units_stackup_and_outline() -> None:
    b = Ipc2581Reader().read(MINI)
    assert b.meta.source_tool == "ipc2581"
    assert b.meta.source_version == "IPC-2581B"
    assert not [d for d in validate_board(b, strict=True) if d.severity == "error"]
    # inch units to metres
    assert b.stackup.total_thickness == pytest.approx(0.0630 * 0.0254)
    ids = [layer.id for layer in b.stackup.layers]
    assert ids == ["L1", "D1", "L2"]
    mats = {m.name: m for m in b.stackup.materials}
    fr4 = mats["FR4"]
    assert fr4.model.dk == 4.2 and fr4.model.df == 0.018
    assert mats["copper"].conductivity == pytest.approx(5.8e7)  # MHO/CM converted
    assert b.stackup.layers[1].construction == "core"
    # outline: 1 x 0.5 inch with a round cutout (arcs tessellated)
    xs = b.outline.polygon.exterior.array
    assert xs[:, 0].max() == pytest.approx(0.0254)
    assert len(b.outline.polygon.holes) == 1
    assert b.outline.edges  # analytic exterior edges kept
    hole = b.outline.polygon.holes[0].array
    r = 0.05 * 0.0254
    assert math.hypot(*(hole[0] - (0.8 * 0.0254, 0.25 * 0.0254))) == pytest.approx(r, rel=1e-3)


def test_mini_rev_b_features() -> None:
    b = Ipc2581Reader().read(MINI)
    assert {n.id for n in b.nets} == {"SIG", "GND"}  # "No Net" is never a net
    assert {n.id: n.kind for n in b.nets}["GND"] == "ground"
    assert len(b.tracks) == 2
    line, arc = b.tracks
    assert line.width == pytest.approx(0.010 * 0.0254)
    assert arc.path[0].kind == "arc" and arc.net == "SIG"
    assert len(b.vias) == 1
    via = b.vias[0]
    assert (via.kind, via.start_layer, via.end_layer, via.net) == ("through", "L1", "L2", "GND")
    ps = {p.id: p for p in b.padstacks}[via.padstack]
    assert ps.drill is not None and ps.drill.diameter == pytest.approx(0.012 * 0.0254)
    assert {layer.layer for layer in ps.layers} == {"L1", "L2"}
    # zone fill keeps its cutout as a hole
    (zone,) = b.zones
    assert zone.net == "GND" and zone.fill_state == "filled"
    assert len(zone.fills[0].polygons[0].holes) == 1
    # pads: nets from PadStack, pin refs, rotation; the non-plated hole becomes an npth pad
    by = {(p.component, p.number): p for p in b.pads}
    assert by[("U1", "1")].net == "SIG" and by[("U1", "1")].rotation == pytest.approx(math.pi / 2)
    assert by[("U1", "2")].padstack != by[("U1", "1")].padstack
    npth = [p for p in b.pads if p.kind == "npth"]
    assert len(npth) == 1 and npth[0].component is None
    c1 = next(c for c in b.components if c.refdes == "C1")
    assert c1.side == "bottom"
    assert {c.refdes for c in b.components} == {"U1", "C1"}


def test_deterministic_ids_across_reads() -> None:
    a = Ipc2581Reader().read(MINI)
    b = Ipc2581Reader().read(MINI)
    assert [t.id for t in a.tracks] == [t.id for t in b.tracks]
    assert a.content_hash() == b.content_hash()


# ---------------------------------------------------------------------- WP-102 boards
BOARDS = sorted(p.parent for p in SYNTHETIC.glob("*/board.ipc2581.xml"))


@pytest.mark.skipif(not BOARDS, reason="WP-102 synthetic boards not present")
@pytest.mark.parametrize("board_dir", BOARDS, ids=[p.name for p in BOARDS])
def test_synthetic_board_matches_expected_yaml(board_dir: Path) -> None:
    exp = yaml.safe_load((board_dir / "expected.yaml").read_text(encoding="utf-8"))
    b = Ipc2581Reader().read(board_dir / "board.ipc2581.xml")
    assert not [d for d in validate_board(b) if d.severity == "error"]
    c = exp["counts"]
    assert len(b.nets) == c["nets"]
    assert len(b.pads) == c["pads"]
    assert len(b.tracks) == c["tracks"] + c.get("arcs", 0)
    assert len(b.components) == c["footprints"]
    assert len(b.zones) == c["zones"]
    n_cu = sum(1 for layer in b.stackup.layers if layer.kind == "conductor")
    assert n_cu == c["copper_layers"]
    kinds = {"through": 0, "blind": 0, "buried": 0}
    for v in b.vias:
        kinds[v.kind] += 1
    ev = c.get("vias", {})
    assert kinds["through"] == ev.get("through", 0)
    # IPC-2581 has no micro-via flag: micro vias come back as blind
    assert kinds["blind"] == ev.get("blind", 0) + ev.get("micro", 0)
    assert kinds["buried"] == ev.get("buried", 0)

    # stackup: ids and thicknesses exact; z offset by the solder mask KiCad's IPC export always
    # adds (even when the board defines none, 10 um at the bottom)
    by_id = {layer.id: layer for layer in b.stackup.layers}
    off = by_id["MB"].thickness if "MB" in by_id else 0.0
    for sl in exp["stackup"]["layers"]:
        layer = by_id[sl["id"]]
        assert layer.thickness == pytest.approx(sl["thickness"], abs=1e-9)
        assert layer.z_top - off == pytest.approx(sl["z_top"], abs=1e-9)
        assert layer.z_bottom - off == pytest.approx(sl["z_bottom"], abs=1e-9)
    mat = exp["materials"]
    dk_ok = [m for m in b.stackup.materials if m.kind == "dielectric" and m.model.dk == mat["dk"]]
    assert dk_ok, "FR-4 Dk from the Spec section"

    # per-layer, per-net copper area against the pcbnew figures recorded by WP-102. Those were
    # taken after a ZONE_FILLER refill while IPC-2581 carries the fill saved in the file, so
    # plane nets differ by up to ~1 % on two boards (cpwg, microstrip_v9); 1.5 % bound here.
    ca = exp["copper_area"]
    stats = board_stats(b)["copper_area"]
    for lid, nets in ca["layers"].items():
        for net, area in nets.items():
            got = stats[lid]["nets"].get(net, 0.0)
            assert got == pytest.approx(area, rel=0.015, abs=2e-9), (lid, net)


def test_backdrill_from_spec_matches_wp102_via_transition() -> None:
    d = SYNTHETIC / "via_transition"
    if not (d / "board.ipc2581.xml").is_file():
        pytest.skip("WP-102 synthetic boards not present")
    b = Ipc2581Reader().read(d / "board.ipc2581.xml")
    exp = yaml.safe_load((d / "expected.yaml").read_text(encoding="utf-8"))
    bd_exp = exp["geometry"]["via_transition"]["single_ended_backdrilled"]["backdrill"]
    (via,) = [v for v in b.vias if v.backdrills]
    assert via.net == "SE2" and via.position == pytest.approx((0.015, -0.035))
    (bd,) = via.backdrills
    assert bd.side == bd_exp["side"]
    assert bd.last_removed_layer == bd_exp["end_layer"]  # L7
    assert bd.must_not_cut == "L6"  # In5.Cu = next layer inward
    assert any(x.code == "INGEST.BACKDRILL_SIZE_UNKNOWN" for x in b.diagnostics)


# ---------------------------------------------------------------------- non-KiCad IPC-2581
def _external_samples() -> list[Path]:
    env = os.environ.get("OA_IPC2581_SAMPLES")
    return sorted(Path(env).glob("*.cvg")) + sorted(Path(env).glob("*.xml")) if env else []


@pytest.mark.skipif(
    not _external_samples(),
    reason="set OA_IPC2581_SAMPLES to a directory with non-KiCad IPC-2581 files "
    "(see packages/oa-ingest-ipc2581/README.md)",
)
@pytest.mark.parametrize("path", _external_samples(), ids=lambda p: p.name)
def test_non_kicad_ipc2581_parses(path: Path) -> None:
    b = Ipc2581Reader().read(path)
    assert not [d for d in validate_board(b) if d.severity == "error"]
    assert b.tracks and b.pads and b.components


def test_module_does_not_import_pcbnew() -> None:
    Ipc2581Reader().read(MINI)
    assert "pcbnew" not in sys.modules
