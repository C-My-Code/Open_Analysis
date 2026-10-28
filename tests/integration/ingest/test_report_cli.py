# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ingest report tool: JSON for agents, text for people, CSV for the diff."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import ingest_report as rep
import ingest_xval as xv
import pytest

BOARD = xv.SYNTHETIC / "via_transition"
pytestmark = pytest.mark.skipif(not BOARD.is_dir(), reason="WP-102 boards not present")


def test_json_report_for_one_board(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out_json, out_csv = tmp_path / "r.json", tmp_path / "r.csv"
    code = rep.main([str(BOARD), "--json", str(out_json), "--csv", str(out_csv)])
    assert code == 0
    assert capsys.readouterr().out == ""  # files requested: nothing on stdout
    doc = json.loads(out_json.read_text(encoding="utf-8"))
    assert doc["schema"] == "oa-ingest-report/1"
    assert doc["summary"]["ok"] is True
    assert doc["summary"]["matrix"] == {
        "via_transition": {"kicad": "whitelisted", "ipc2581": "whitelisted"}
    }
    (b,) = doc["boards"]
    assert b["native"]["counts"]["vias"] == xv.load_expected(BOARD)["counts"]["vias"]
    assert b["native"]["counts"]["backdrilled_vias"] == 1
    assert b["ipc2581"]["counts"]["backdrilled_vias"] == 1
    assert b["failures"] == []
    assert {w["exception"] for w in b["whitelisted"]} >= {
        "ipc-backdrill-diameter-missing",
        "expected-area-ignores-backdrill",
    }
    assert all(w["cause"] for w in b["whitelisted"])
    assert b["native"]["copper_area_m2"]["L1"] > 0
    assert doc["meta"]["tolerances"]["area_rel_native_vs_ipc2581"] == 5e-3
    with out_csv.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == sum(b["findings"]["total"]["counts"].values())


def test_text_report_names_the_board_and_the_causes(capsys: pytest.CaptureFixture[str]) -> None:
    assert rep.main([str(BOARD), "--text"]) == 0
    text = capsys.readouterr().out
    assert "Result: OK" in text
    assert "via_transition" in text and "whitelisted" in text
    assert "ipc-backdrill-diameter-missing" in text


def test_a_failing_board_gives_exit_status_1(tmp_path: Path) -> None:
    """Without the exception list the known differences are failures."""
    res = xv.run_board(
        "via_transition",
        BOARD / "board.kicad_pcb",
        BOARD / "board.ipc2581.xml",
        xv.load_expected(BOARD),
        exceptions=[],
    )
    report = rep.build_report([res])
    assert report["summary"]["ok"] is False
    assert report["summary"]["failing_boards"] == ["via_transition"]
    text = rep.format_text(report)
    assert "FAIL" in text and "Result: FAIL" in text
    assert report["boards"][0]["fidelity"]["ipc2581"] == "fail"


def test_stale_zones_show_up_in_the_report(tmp_path: Path) -> None:
    split = xv.SYNTHETIC / "split_plane"
    text = (split / "board.kicad_pcb").read_text(encoding="utf-8")
    i = text.index('(zone\n\t\t(net "GND")\n\t\t(layer "B.Cu")')
    old = "(xy 0 0) (xy 100 0) (xy 100 70) (xy 0 70)"
    j = text.index(old, i)
    edited = text[:j] + "(xy 0 0) (xy 50 0) (xy 50 70) (xy 0 70)" + text[j + len(old) :]
    pcb = tmp_path / "edited.kicad_pcb"
    pcb.write_text(edited, encoding="utf-8", newline="\n")
    (res,) = rep.run([pcb], ipc="none")
    doc = rep.result_to_dict(res)
    assert doc["native"]["zones"]["fill_outside_outline"]
    assert "STALE?" in rep.format_text(rep.build_report([res]))


def test_runs_as_a_script(tmp_path: Path) -> None:
    out = tmp_path / "r.json"
    cp = subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(rep.__file__)),
            str(xv.SYNTHETIC / "microstrip"),
            "--json",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert cp.returncode == 0, cp.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]["boards"] == 1


def test_usage_errors() -> None:
    with pytest.raises(SystemExit):
        rep.main([])
    assert rep.main(["no/such/board.kicad_pcb"]) == 2
