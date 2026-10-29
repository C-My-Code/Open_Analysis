# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""End to end on a real Palace (slow, ``OA_RUN_SLOW=1``): the microstrip thru fixture through the
``oa_jobs`` WSL (or local) runner, compared with the openEMS result of WP-205.

The run is boxed by ``oa-palace-run`` (6 GB, 4 cores, 2 MPI ranks). Coarse mesh, order 1, AMS:
about 230k tets, 3 sweep points, about 170 s and 3.6 GB. Skipped when no Palace install is found
(see the package README).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from palace_helpers import microstrip_wave_problem

from oa_em_palace import PalaceAdapter, discover
from oa_jobs.runners import make_runner
from oa_jobs.tools import ToolsConfig

pytestmark = [pytest.mark.external("palace"), pytest.mark.slow]

# openEMS reference (WP-205, microstrip thru 25 mm, 0.38 mm on 0.2 mm FR-4, lumped ports)
OPENEMS_IL_10GHZ_DB = 0.74
OPENEMS_Z0_OHM = 50.7
IL_TOL_DB = 0.6
C0 = 299792458.0


class _Never:
    def is_cancelled(self) -> bool:
        return False


@pytest.fixture(scope="module")
def thru(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    info = discover()
    if info is None:
        pytest.skip("Palace not found (see packages/oa-em-palace/README.md)")
    problem = microstrip_wave_problem(n_points=3)  # 1, 5.5, 10 GHz
    work = tmp_path_factory.mktemp("palace_thru")
    adapter = PalaceAdapter(mpi_ranks=2, memory_gb=6, cpu_percent=400)
    run = adapter.prepare(problem, work, info)
    assert not [d for d in adapter.check(problem) if d.severity == "error"]
    tools = ToolsConfig({"palace": {"runner": info.runner, "path": str(info.path)}})
    result = make_runner(info.runner, tools).run(run.commands[0], lambda f, m: None, _Never())
    out = adapter.collect(problem, run, [result])
    assert out.network is not None, [w.message for w in out.warnings]
    meta = json.loads((work / "out" / f"{problem.name}.json").read_text(encoding="utf-8"))
    return {"out": out, "net": out.network, "meta": meta, "run": run}


def test_insertion_loss_matches_openems(thru: dict[str, Any], record_property: Any) -> None:
    net = thru["net"]
    f = np.asarray(net.freqs.array)
    s = np.asarray(net.s.array)
    il = -20 * np.log10(abs(s[-1, 1, 0]))
    record_property("freq_GHz", float(f[-1] / 1e9))
    record_property("IL_10GHz_dB_palace", round(float(il), 3))
    record_property("IL_10GHz_dB_openems", OPENEMS_IL_10GHZ_DB)
    assert f[-1] == pytest.approx(10e9)
    assert abs(il - OPENEMS_IL_10GHZ_DB) <= IL_TOL_DB
    assert -20 * np.log10(abs(s[0, 1, 0])) < 0.4  # nearly lossless at 1 GHz
    assert np.all(np.diff(-20 * np.log10(abs(s[:, 1, 0]))) > 0)  # loss grows with frequency


def test_wave_port_match_and_delay(thru: dict[str, Any], record_property: Any) -> None:
    """S11 against the port's own modal impedance is the Z0 check Palace 0.18 allows: it exports no
    Z0(f) for wave ports without GSLIB. |S11| < -30 dB means the two modal references agree with
    each other and the line is uniform; the delay checks eps_eff against Hammerstad-Jensen."""
    net = thru["net"]
    f = np.asarray(net.freqs.array)
    s = np.asarray(net.s.array)
    s11 = 20 * np.log10(abs(s[:, 0, 0]))
    record_property("S11_worst_dB", round(float(s11.max()), 2))
    record_property("openems_Z0_ohm", OPENEMS_Z0_OHM)
    assert s11.max() < -30.0
    from oa_testkit import analytic

    z0_hj, eeff = analytic.hammerstad_jensen(0.38 / 0.2, 4.21)
    expected = 25e-3 * math.sqrt(eeff) / C0
    # three points are too sparse to unwrap: compare the phase after removing the expected delay
    w = 2 * np.pi * f
    resid = np.angle(s[:, 1, 0] * np.exp(1j * w * expected))
    record_property("expected_delay_ps", round(expected * 1e12, 1))
    record_property("phase_residual_deg", [round(float(math.degrees(r)), 1) for r in resid])
    assert np.all(abs(resid) < 0.05 * w * expected)  # delay within 5 % of Hammerstad-Jensen
    assert z0_hj == pytest.approx(OPENEMS_Z0_OHM, rel=0.03)


def test_network_is_passive_reciprocal_and_reports_cost(
    thru: dict[str, Any], record_property: Any
) -> None:
    net, meta, run = thru["net"], thru["meta"], thru["run"]
    s = np.asarray(net.s.array)
    assert np.linalg.svd(s, compute_uv=False).max() < 1.01
    assert np.abs(s[:, 0, 1] - s[:, 1, 0]).max() < 0.02
    assert Path(net.touchstone.name).suffix == ".s2p"
    assert net.provenance.solver == "palace" and net.provenance.solver_version.startswith("0.18")
    assert meta["wall_s"] > 0 and meta["peak_mem_mb"] is not None
    assert meta["peak_mem_mb"] < 6 * 1024  # inside the hard limit
    record_property("runtime_s", meta["wall_s"])
    record_property("peak_mem_mb", round(meta["peak_mem_mb"]))
    record_property("tets", run.mesh_stats["cells"])
    record_property("dofs", run.mesh_stats["dofs"])
    assert thru["out"].convergence_log is not None
