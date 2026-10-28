# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bridge tests against a real KiCad 10 install (marker ``kicad``)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

import oa_kicad_bridge as kb
import oa_kicad_bridge.bridge as bridge_mod
from oa_ingest_ipc2581 import Ipc2581Reader
from oa_model import validate_board
from oa_model.setup import ReadOptions
from oa_testkit.golden import assert_close_to_golden

pytestmark = pytest.mark.kicad

REPO = Path(__file__).resolve().parents[3]
SYN = REPO / "tests" / "boards" / "kicad10" / "microstrip" / "board.kicad_pcb"


@pytest.fixture(scope="module")
def tools() -> kb.KiCadTools:
    t = kb.KiCadTools.discover()
    if t.python is None:
        pytest.skip("KiCad's Python not found")
    return t


@pytest.fixture(scope="module")
def ecc83(tools: kb.KiCadTools) -> Path:
    assert tools.cli is not None
    p = tools.cli.parent.parent / "share" / "kicad" / "demos" / "ecc83" / "ecc83-pp.kicad_pcb"
    if not p.is_file():
        pytest.skip("ecc83 demo not installed")
    return p


def test_version_and_drc(tools: kb.KiCadTools, tmp_path: Path) -> None:
    assert (tools.version() or "").startswith("10.")
    if not SYN.is_file():
        pytest.skip("WP-102 synthetic boards not present")
    work = tmp_path / "b.kicad_pcb"
    shutil.copy(SYN, work)
    shutil.copy(SYN.with_name("board.kicad_pro"), tmp_path / "b.kicad_pro")
    rep = kb.drc(work, tools=tools)
    assert rep.count("error") == 0  # WP-102 acceptance: DRC clean


def test_upgrade_keeps_board_readable(tools: kb.KiCadTools, tmp_path: Path, ecc83: Path) -> None:
    work = tmp_path / "b.kicad_pcb"
    shutil.copy(ecc83, work)
    assert kb.upgrade(work, tools=tools, force=True) == work
    assert kb.inspect_zones(work, tools=tools)["zones"] == 1


def test_exports(tools: kb.KiCadTools, tmp_path: Path, ecc83: Path) -> None:
    ipc = kb.export_ipc2581(ecc83, tmp_path / "e.xml", tools=tools)
    assert Ipc2581Reader.can_read(ipc)
    odb = kb.export_odb(ecc83, tmp_path / "e.zip", tools=tools)
    assert odb.stat().st_size > 1000


def test_refill_matches_saved_gui_fill(tools: kb.KiCadTools, tmp_path: Path, ecc83: Path) -> None:
    """Empty the zone with pcbnew, refill through the bridge, compare with the recorded area."""
    unfilled = tmp_path / "un.kicad_pcb"
    rep = bridge_mod._helper("unfill", [ecc83, unfilled], tools, 300.0)  # test support mode
    assert rep["unfilled"] == 1
    opts = ReadOptions(refill_zones="if_unfilled")
    path, refilled = kb.prepare_read(unfilled, opts, tools=tools, work_dir=tmp_path / "w")
    assert refilled and path != unfilled
    areas = kb.inspect_zones(path, tools=tools)["areas"]
    assert [a["net"] for a in areas] == ["GND"]
    assert_close_to_golden(
        {"zone_area": {"GND_bottom": areas[0]["area_m2"]}}, "ingest_ipc2581", "refill_ecc83"
    )
    # a filled board is left alone by if_unfilled
    same, again = kb.prepare_read(path, opts, tools=tools)
    assert not again and same == path


def test_refill_zones_function_writes_output(
    tools: kb.KiCadTools, tmp_path: Path, ecc83: Path
) -> None:
    rep = kb.refill_zones(ecc83, tmp_path / "out" / "r.kicad_pcb", tools=tools)
    assert rep["zones"] == 1 and (tmp_path / "out" / "r.kicad_pcb").is_file()


def _altium_samples() -> list[Path]:
    env = os.environ.get("OA_ALTIUM_SAMPLES")
    return sorted(Path(env).glob("*.PcbDoc")) if env else []


@pytest.mark.skipif(
    not _altium_samples(),
    reason="set OA_ALTIUM_SAMPLES to a directory with .PcbDoc files "
    "(KiCad's qa/data/pcbnew/plugins/altium, see the README)",
)
@pytest.mark.parametrize("sample", _altium_samples(), ids=lambda p: p.name)
def test_altium_round_trip_to_valid_board(
    tools: kb.KiCadTools, tmp_path: Path, sample: Path
) -> None:
    """PcbDoc -> kicad-cli import -> (IPC-2581 export -> our reader) gives a valid Board whose
    counts match the importer's own statistics."""
    imp = kb.import_foreign_report(sample, "altium", out_dir=tmp_path / "imp", tools=tools)
    assert imp.source_format == "Altium Designer"
    assert not [d for d in imp.diagnostics if d.severity == "error"]
    ipc = kb.export_ipc2581(imp.path, tmp_path / "x.xml", tools=tools)
    board = Ipc2581Reader().read(ipc)
    assert not [d for d in validate_board(board) if d.severity == "error"]
    assert len(board.tracks) >= imp.statistics["tracks"] * 0.95
    assert len(board.components) == imp.statistics["footprints"]
    assert len(board.zones) > 0 and len(board.vias) > 0
    assert imp.statistics["vias"] >= len(board.vias) * 0.95


def test_pcbnew_not_in_calling_process() -> None:
    assert "pcbnew" not in sys.modules
