# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Output parsing and ``collect`` on committed raw Palace 0.18.1 output (no Palace needed)."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
from palace_helpers import GOLDEN, microstrip_wave_problem

from oa_em_palace import PalaceAdapter
from oa_em_palace.parse import parse_footer, read_port_s
from oa_framework.em import PreparedRun
from oa_framework.external import ExternalCommand, ExternalResult
from oa_results.io.touchstone import from_touchstone

RAW = GOLDEN / "raw" / "microstrip_thru_wave"
# raw run: 3 points (1, 5.5, 10 GHz), wave ports, 228k tets, p = 1, AMS, 2 MPI ranks
S21_DB = (-0.146, -0.717, -1.238)  # read off port-S.csv
S11_DB_MAX = -36.0


def test_read_port_s_columns_units_and_ordering() -> None:
    sp = read_port_s(RAW / "postpro" / "port-S.csv")
    assert sp.freqs == pytest.approx([1e9, 5.5e9, 10e9])
    assert sp.s.shape == (3, 2, 2) and sp.excited == (0, 1)
    assert 20 * np.log10(abs(sp.s[:, 1, 0])) == pytest.approx(S21_DB, abs=2e-3)
    assert 20 * np.log10(abs(sp.s[:, 0, 0])).max() < S11_DB_MAX
    assert np.allclose(sp.s[:, 1, 0], sp.s[:, 0, 1], atol=1e-2)  # reciprocal (GMRES 1e-6)


def test_read_port_s_rejects_garbage(tmp_path: Path) -> None:
    bad = tmp_path / "port-S.csv"
    bad.write_text("a, b\n1, 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="frequency"):
        read_port_s(bad)
    bad.write_text("f (GHz), x\n1, 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="column pairs"):
        read_port_s(bad)


def test_parse_footer() -> None:
    f = parse_footer((RAW / "stdout_tail.txt").read_text(encoding="utf-8"))
    assert f.version == "0.18.1" and f.wall_s == pytest.approx(168.55)
    assert f.peak_mem_kb == 3573984
    assert parse_footer("nothing here").wall_s is None


def _run(tmp_path: Path, returncode: int = 0) -> tuple[PreparedRun, list[ExternalResult]]:
    work = tmp_path / "job"
    shutil.copytree(RAW / "postpro", work / "postpro")
    cmd = ExternalCommand(program="palace", args=("config.json",), workdir=work)
    run = PreparedRun(workdir=work, commands=(cmd,), mesh_stats={"cells": 228216}, files=())
    res = ExternalResult(
        returncode=returncode,
        stdout_tail=(RAW / "stdout_tail.txt").read_text(encoding="utf-8"),
        stderr_tail="boom",
        wall_s=170.0,
        runner="wsl",
    )
    return run, [res]


def test_collect_builds_network_touchstone_and_sidecar(tmp_path: Path) -> None:
    problem = microstrip_wave_problem()
    run, results = _run(tmp_path)
    out = PalaceAdapter().collect(problem, run, results)
    net = out.network
    assert net is not None and out.solver_version == "0.18.1"
    assert net.port_names == ("P1", "P2") and net.provenance is not None
    assert net.provenance.solver == "palace"
    f = np.asarray(net.freqs.array)
    s = np.asarray(net.s.array)
    assert f == pytest.approx([1e9, 5.5e9, 10e9])
    assert 20 * np.log10(abs(s[2, 1, 0])) == pytest.approx(-1.238, abs=2e-3)
    assert net.quality.verdict in {"pass", "warn", "fail"}
    ts = run.workdir / "out" / "microstrip_thru_25mm_wave.s2p"
    assert net.touchstone is not None and net.touchstone.name == ts.name
    back = from_touchstone(ts)
    assert np.allclose(np.asarray(back.s.array), s, atol=1e-9)
    side = (run.workdir / "out" / "microstrip_thru_25mm_wave.json").read_text(encoding="utf-8")
    assert '"peak_mem_mb"' in side and '"wall_s": 168.55' in side
    assert out.convergence_log is not None and len(out.convergence_log.inline or ()) == 1
    assert {w.code for w in out.warnings} == set()


def test_collect_reports_failures(tmp_path: Path) -> None:
    problem = microstrip_wave_problem()
    run, results = _run(tmp_path, returncode=1)
    out = PalaceAdapter().collect(problem, run, results)
    assert out.network is None
    assert out.warnings[-1].code == "PALACE.RUN_FAILED" and "boom" in (out.warnings[-1].hint or "")
    run2, results2 = _run(tmp_path / "x")
    (run2.workdir / "postpro" / "port-S.csv").unlink()
    assert PalaceAdapter().collect(problem, run2, results2).warnings[-1].code == "PALACE.NO_OUTPUT"
    assert PalaceAdapter().collect(problem, run2, []).warnings[-1].code == "PALACE.RESULT_COUNT"
