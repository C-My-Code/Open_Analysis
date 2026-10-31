# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""M1 end to end through the real ``oa`` CLI (subprocess, ``--json``), from a ``.kicad_pcb``.

Per board of WP-102 plus ``cm5_minima``: ``oa init``, ingest, ERC (SARIF), impedance profile,
DC IR drop (power boards), HTML report; the EM step (openEMS) is ``external``. The tests assert
on structure and accuracy classes. Numbers belong to the golden suites of the analyses.

``cm5_minima`` is the KiCad demo board (``$KICAD_DEMOS``) and is ``slow``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from e2e_helpers import POWER_BOARDS, Project, accuracy_classes, job_of, stage_em_project

SARIF_VERSION = "2.1.0"


def test_init_writes_the_project_files(project: Project) -> None:
    assert project.init["ok"] is True
    created = [Path(p) for p in project.init["data"]["created"]]
    assert {p.name for p in created} >= {"oa-project.yaml", "physics.yaml", "intent.yaml"}
    assert all(p.is_file() for p in created)


def test_ingest_reports_a_board_summary(project: Project) -> None:
    env = project.step("ingest", "ingest", project.board.name)
    assert env["ok"] is True
    data = env["data"]
    assert data["source_tool"] == "kicad" and data["board_hash"].startswith("sha256:")
    summary = data["summary"]
    assert summary["layer_count"] >= 2 and summary["size_m"][0] > 0
    assert summary["copper_layers"][0]["id"] == "L1"


def test_erc_writes_sarif(project: Project) -> None:
    env = project.erc()
    job = job_of(env)
    summary = project.summary(env)
    assert summary["analysis"] == "erc" and summary["status"] in ("ok", "warning")
    assert summary["text"]
    assert accuracy_classes(summary) == {"screening"}
    sarif = json.loads((project.cli.cwd / "erc.sarif").read_text(encoding="utf-8"))
    assert sarif["version"] == SARIF_VERSION and len(sarif["runs"]) == 1
    run = sarif["runs"][0]
    assert run["tool"]["driver"]["name"]
    assert job["result_id"]
    for res in run["results"]:
        assert res["ruleId"] and res["level"] in ("error", "warning", "note", "none")


def test_erc_violations_page_matches_the_sarif(project: Project) -> None:
    env = project.erc()
    rid = job_of(env)["result_id"]
    page = project.cli.run("results", "violations", rid, "--limit", "5")
    assert page["ok"] is True
    total = page["data"]["total"]
    sarif = json.loads((project.cli.cwd / "erc.sarif").read_text(encoding="utf-8"))
    assert total == len(sarif["runs"][0]["results"])
    assert len(page["data"]["items"]) == min(5, total)


def test_impedance_profile(project: Project) -> None:
    env = project.impedance()
    if env["ok"] is False:
        # a board without signal nets: a structured error, never a traceback
        err = env["errors"][0]
        assert err["code"] and err["message"] and "path" in err and "hint" in err
        pytest.skip(f"no impedance profile for {project.name}: {err['code']}")
    summary = project.summary(env)
    assert summary["analysis"] == "impedance.profile" and summary["text"]
    assert accuracy_classes(summary) <= {"reference", "validated", "screening", "estimate"}


def test_dc_ir_drop(project: Project) -> None:
    if project.name not in POWER_BOARDS:
        pytest.skip("not a power board")
    env = project.dc()
    summary = project.summary(env)
    assert summary["analysis"] == "pi.dc" and summary["status"] == "ok"
    assert "drop" in summary["text"]
    assert accuracy_classes(summary) == {"validated"}


def test_html_report(project: Project) -> None:
    """The report: the DC result on power boards, the ERC result otherwise."""
    env = project.dc() if project.name in POWER_BOARDS else project.erc()
    rid = job_of(env)["result_id"]
    out = project.cli.cwd / "report.html"
    exp = project.cli.run("results", "export", rid, "--format", "html", "--out-path", str(out))
    assert exp["ok"] is True and exp["data"]["media_type"] == "text/html"
    assert out.is_file() and out.stat().st_size == exp["data"]["size"] > 0
    assert "<!doctype html" in out.read_text(encoding="utf-8").lower()


@pytest.mark.external("openems")
@pytest.mark.slow
def test_em_sparams_via_board(tmp_path: Path) -> None:
    """Board -> openEMS S-parameters of a 10 mm cut of a 50 mm microstrip (needs openEMS)."""
    cli, cfg = stage_em_project(tmp_path)
    env = cli.run("run", "--analysis", "em.sparams", "--config", cfg, "--timeout", "1200")
    rid = job_of(env)["result_id"]
    summary = cli.run("results", "summary", rid)["data"]
    assert summary["analysis"] == "em.sparams" and summary["text"]
    assert accuracy_classes(summary)


def test_em_dry_run_validates_without_a_solver(tmp_path: Path) -> None:
    """The EM config and estimate path works with no openEMS installed."""
    cli, cfg = stage_em_project(tmp_path)
    dry = cli.run("run", "--analysis", "em.sparams", "--config", cfg, "--dry-run")
    assert dry["data"]["valid"] is True and dry["data"]["job"] is None
    est = dry["data"]["estimate"]
    assert est["external"] is True and "openems-python" in est["needs"]
