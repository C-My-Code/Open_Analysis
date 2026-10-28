# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Adapter contract: capabilities, check, prepare snapshots, collect on committed raw output."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from oa_em_openems import OpenEMSAdapter
from oa_em_openems import adapter as adapter_mod
from oa_em_openems.scene import mesh_summary
from oa_em_openems.touchstone import read_touchstone
from oa_framework.em import SolverAdapter
from oa_framework.external import ExternalResult, ToolInfo
from oa_model.em import EMProblem
from oa_results.quality import QualityCriteria, QualityReport
from oa_testkit.emproblems import (
    microstrip_thru_problem,
    stripline_thru_problem,
    via_transition_problem,
)
from oa_testkit.golden import assert_close_to_golden, update_mode

GOLDEN = Path(__file__).resolve().parents[3] / "tests" / "golden" / "em_openems"
SUITE = "em_openems"
TOOL = ToolInfo(program="openems-python", runner="local", path=None, version="0.37.0")
FIXTURES = {
    "microstrip_thru": microstrip_thru_problem,
    "stripline_thru": stripline_thru_problem,
    "via_transition": via_transition_problem,
}


def _pass_report(net: Any) -> QualityReport:
    return QualityReport(
        verdict="pass",
        pqm=100.0,
        rqm=100.0,
        cqm=100.0,
        max_singular_value=0.99,
        worst_passivity_freq=None,
        reciprocity_max_err=0.0,
        dc="extrapolated",
        f_min=float(net.f[0]),
        f_max=float(net.f[-1]),
        n_freqs=len(net.f),
        criteria=QualityCriteria(),
    )


def _normalize(obj: Any) -> Any:
    return json.loads(json.dumps(obj))


def _close(a: Any, b: Any, path: str = "") -> None:
    if isinstance(a, dict):
        assert set(a) == set(b), path
        for k in a:
            _close(a[k], b[k], f"{path}/{k}")
    elif isinstance(a, list):
        assert len(a) == len(b), f"{path}: {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            _close(x, y, f"{path}[{i}]")
    elif isinstance(a, float):
        assert a == pytest.approx(b, rel=1e-9, abs=1e-12), path
    else:
        assert a == b, path


def test_adapter_satisfies_the_protocol_and_declares_capabilities() -> None:
    ad = OpenEMSAdapter()
    assert isinstance(ad, SolverAdapter)
    assert ad.name == "openems" and ad.tool == "openems-python"
    caps = ad.capabilities()
    assert caps.wave_port_mode == "tl_probe" and caps.excitations_per_run == "one"
    assert caps.physics == {"fullwave_driven"} and not caps.farfield
    assert OpenEMSAdapter(farfield=True).capabilities().farfield


def test_check_flags_unsupported_physics_and_ports() -> None:
    from oa_model.em import Eigenmode

    p = microstrip_thru_problem().model_copy(update={"physics": Eigenmode(f_target=1e9)})
    codes = {d.code for d in OpenEMSAdapter().check(p)}
    assert "OPENEMS.PHYSICS" in codes
    assert not [
        d for d in OpenEMSAdapter().check(microstrip_thru_problem()) if d.severity == "error"
    ]


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_prepare_matches_snapshots(name: str, tmp_path: Path) -> None:
    problem: EMProblem = FIXTURES[name]()
    prepared = OpenEMSAdapter().prepare(problem, tmp_path, TOOL)
    assert len(prepared.commands) == 2  # one run per excitation
    for cmd in prepared.commands:
        assert cmd.program == "openems-python" and cmd.args == ("sim.py",)
        assert cmd.progress_parser == "openems" and "ports.npz" in cmd.outputs
        assert (cmd.workdir / "sim.py").is_file()
    stats = prepared.mesh_stats
    assert {"cells", "est_memory_gb", "est_time_s", "lines", "min_cell_um"} <= set(stats)

    scene = json.loads((prepared.commands[0].workdir / "scene.json").read_text())
    script = (prepared.commands[0].workdir / "sim.py").read_text()
    from oa_em_openems.adapter import prepared_scene

    summary = mesh_summary(prepared_scene(problem))
    flat = {
        "nx": summary["x"]["n_lines"],
        "ny": summary["y"]["n_lines"],
        "nz": summary["z"]["n_lines"],
        "cells": summary["cells"],
        "mesh_hash": summary["mesh_hash"],
        "min_cell_x": summary["x"]["min_cell"],
        "min_cell_y": summary["y"]["min_cell"],
        "min_cell_z": summary["z"]["min_cell"],
    }
    assert_close_to_golden(
        flat,
        SUITE,
        f"snapshot_{name}",
        source="regression snapshot of oa_testkit.emproblems fixture; see README (mesh rules)",
        inputs={"fixture": name},
        rtol=1e-9,
    )
    case_dir = GOLDEN / f"snapshot_{name}"
    if update_mode():
        (case_dir / "scene.json").write_text(
            json.dumps(scene, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        (case_dir / "sim.py").write_text(script, encoding="utf-8", newline="\n")
    ref_scene = json.loads((case_dir / "scene.json").read_text(encoding="utf-8"))
    _close(_normalize(scene), ref_scene)
    ref_script = (case_dir / "sim.py").read_text(encoding="utf-8")
    assert script.replace("\r\n", "\n") == ref_script.replace("\r\n", "\n")


def test_prepare_is_deterministic(tmp_path: Path) -> None:
    p = microstrip_thru_problem()
    a = OpenEMSAdapter().prepare(p, tmp_path / "a", TOOL)
    b = OpenEMSAdapter().prepare(p, tmp_path / "b", TOOL)
    for rel in a.files:
        assert (tmp_path / "a" / rel).read_bytes() == (tmp_path / "b" / rel).read_bytes()
    assert a.mesh_stats == b.mesh_stats


def _prepared_from_raw(tmp_path: Path, problem: EMProblem) -> Any:
    raw = GOLDEN / "raw" / "microstrip_thru"
    work = tmp_path / "job"
    shutil.copytree(raw, work)
    prepared = OpenEMSAdapter().prepare(problem, tmp_path / "scratch", TOOL)
    cmds = tuple(c.model_copy(update={"workdir": work / c.workdir.name}) for c in prepared.commands)
    return prepared.model_copy(update={"workdir": work, "commands": cmds})


def _ok(stdout: str = "") -> ExternalResult:
    return ExternalResult(
        returncode=0,
        stdout_tail=stdout,
        stderr_tail="",
        wall_s=1.0,
        runner="local",
        tool_version="0.37.0",
    )


def test_collect_reproduces_committed_s2p(tmp_path: Path) -> None:
    problem = microstrip_thru_problem()
    prepared = _prepared_from_raw(tmp_path, problem)
    stdout = (GOLDEN / "raw" / "microstrip_thru" / "exc_P1" / "stdout_tail.txt").read_text()
    out = OpenEMSAdapter(assessor=_pass_report).collect(
        problem, prepared, [_ok(stdout), _ok(stdout)]
    )
    net = out.network
    assert net is not None and out.solver_version.startswith("0.37")
    assert net.port_names == ("P1", "P2") and net.quality.verdict == "pass"
    produced = prepared.workdir / "out" / "microstrip_thru_25mm_lumped.s2p"
    assert net.touchstone is not None and net.touchstone.name == produced.name
    f, s, z0 = read_touchstone(produced)
    f_ref, s_ref, z_ref = read_touchstone(GOLDEN / "collect_microstrip_thru" / "expected.s2p")
    assert z0 == z_ref == [50.0, 50.0]
    assert np.allclose(f, f_ref, rtol=1e-12, atol=0.0)
    assert np.abs(s - s_ref).max() <= 1e-9  # acceptance: reproduces the committed .s2p to 1e-9
    assert out.port_impedance is not None and out.convergence_log is not None
    assert (prepared.workdir / "out" / "microstrip_thru_25mm_lumped.json").is_file()
    k = 200  # 10 GHz
    assert_close_to_golden(
        {
            "s21_re": float(s[k, 1, 0].real),
            "s21_im": float(s[k, 1, 0].imag),
            "s11_abs": float(abs(s[k, 0, 0])),
        },
        SUITE,
        "collect_microstrip_thru",
        source=(
            "regression: real openEMS 0.37.0 run of oa_testkit.microstrip_thru_problem "
            "(raw/microstrip_thru, see README)"
        ),
        inputs={"f_hz": 10e9},
        rtol=1e-9,
        atol=1e-12,
    )


def test_collect_without_quality_gate_is_not_silently_passing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(adapter_mod, "default_assessor", lambda: None)
    problem = microstrip_thru_problem()
    prepared = _prepared_from_raw(tmp_path, problem)
    out = OpenEMSAdapter().collect(problem, prepared, [_ok(), _ok()])
    assert out.network is not None
    assert out.network.quality.verdict == "fail"
    assert "not available" in out.network.quality.notes[0]


def test_collect_reports_failed_runs_as_errors(tmp_path: Path) -> None:
    problem = microstrip_thru_problem()
    prepared = _prepared_from_raw(tmp_path, problem)
    bad = ExternalResult(
        returncode=3, stdout_tail="", stderr_tail="boom", wall_s=0.1, runner="local"
    )
    out = OpenEMSAdapter(assessor=_pass_report).collect(problem, prepared, [bad, _ok()])
    assert out.network is None
    assert out.warnings[0].severity == "error" and out.warnings[0].code == "OPENEMS.RUN_FAILED"
    short = OpenEMSAdapter(assessor=_pass_report).collect(problem, prepared, [_ok()])
    assert short.warnings[0].code == "OPENEMS.RESULT_COUNT"
