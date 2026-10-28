# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``em.sparams`` flow with a mock solver adapter (no external tool needed)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pytest

from oa_em import EmSparams
from oa_framework.em import AdapterCapabilities, EMSolveOutput, PreparedRun
from oa_framework.external import ExternalCommand, ExternalResult, ToolInfo
from oa_geometry import BoardGeometry
from oa_model.arrays import Floats, NDArray
from oa_model.common import Diagnostic
from oa_model.em import EMProblem
from oa_model.setup import AnalysisSetup
from oa_results.network import NetworkResult
from oa_results.quality import QualityCriteria, QualityReport
from oa_results.tables import ColumnSpec, TableResult
from oa_testkit import builders as bld
from oa_testkit.harness import InlineRunContext, run_inline_full

CONFIG = {
    "region": {"nets": ["SIG"], "bbox": [1e-3, -3e-3, 24e-3, 3e-3]},
    "frequency": {"f_min": 0, "f_max": 10e9, "n_points": 51},
}


class MockAdapter:
    """Ideal thru line; ``gain`` > 1 makes it non-passive."""

    name: ClassVar[str] = "mock"
    tool: ClassVar[str] = "mock-solver"

    def __init__(self, gain: float = 1.0, final_energy_db: float = -40.0) -> None:
        self.gain = gain
        self.final_energy_db = final_energy_db

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            physics=frozenset({"fullwave_driven"}),
            ports=frozenset({"wave", "lumped"}),
            wave_port_mode="tl_probe",
            boundaries=frozenset({"pec", "absorbing"}),
            dispersive_dielectric="none",
            surface_roughness=False,
            sheet_conductors=True,
            excitations_per_run="one",
            farfield=False,
            platforms=frozenset({"windows", "linux"}),
        )

    def check(self, problem: EMProblem) -> tuple[Diagnostic, ...]:
        return ()

    def prepare(self, problem: EMProblem, workdir: Path, tool: ToolInfo) -> PreparedRun:
        cmds = []
        for p in problem.ports:
            d = workdir / f"exc_{p.name}"
            d.mkdir(parents=True, exist_ok=True)
            cmds.append(
                ExternalCommand(program=self.tool, args=("x",), workdir=d, outputs=("out.txt",))
            )
        return PreparedRun(
            workdir=workdir,
            commands=tuple(cmds),
            mesh_stats={"cells": 1000, "est_time_s": 100.0, "est_memory_gb": 0.1},
            files=(),
        )

    def collect(
        self, problem: EMProblem, run: PreparedRun, results: Sequence[ExternalResult]
    ) -> EMSolveOutput:
        assert all((c.workdir / "out.txt").is_file() for c in run.commands)
        f = np.linspace(0.0, problem.frequency.f_max, problem.frequency.n_points)
        tau = 0.2e-9
        s = np.zeros((f.size, 2, 2), dtype=complex)
        s[:, 1, 0] = s[:, 0, 1] = self.gain * np.exp(-2j * np.pi * f * tau)
        crit = QualityCriteria()
        rep = QualityReport(
            verdict="pass",
            pqm=100.0,
            rqm=100.0,
            cqm=100.0,
            max_singular_value=1.0,
            worst_passivity_freq=None,
            reciprocity_max_err=0.0,
            dc="present",
            f_min=0.0,
            f_max=float(f[-1]),
            n_freqs=int(f.size),
            criteria=crit,
        )
        net = NetworkResult(
            freqs=Floats(f),
            s=NDArray(s),
            z0=NDArray(np.array([50.0, 50.0], dtype=complex)),
            port_names=tuple(p.name for p in problem.ports),
            port_map=(),
            quality=rep,
        )
        conv = TableResult(
            columns=(
                ColumnSpec(name="run", dtype="str", role="id"),
                ColumnSpec(name="timestep", dtype="i8"),
                ColumnSpec(name="energy_db", dtype="f8", unit="dB"),
            ),
            inline=tuple(("exc", 100, self.final_energy_db) for _ in range(1)),
        )
        return EMSolveOutput(
            network=net,
            port_impedance=None,
            farfield=None,
            convergence_log=conv,
            solver_version="mock-1",
        )


@pytest.fixture
def fake_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    def run_external(self: Any, cmd: ExternalCommand) -> ExternalResult:
        (cmd.workdir / "out.txt").write_text("ok")
        return ExternalResult(
            returncode=0, stdout_tail="", stderr_tail="", wall_s=1.5, runner="fake"
        )

    monkeypatch.setattr(InlineRunContext, "run_external", run_external)


def _run(adapter: MockAdapter, config: dict[str, Any] | None = None) -> Any:
    setup = AnalysisSetup(
        schema="oa-setup/1", name="t", analysis="em.sparams", config=config or CONFIG
    )
    return run_inline_full(
        EmSparams(adapter=adapter),  # type: ignore[arg-type]
        bld.microstrip_board(),
        setup,
        geometry=BoardGeometry.build,
    )


def test_plan_has_prepare_and_one_run_per_excitation(fake_runner: None) -> None:
    run = _run(MockAdapter())
    assert run.order[0] == "prepare"
    assert sorted(t for t in run.order if t.startswith("run:")) == ["run:base:0", "run:base:1"]
    t = next(t for t in run.plan.tasks if t.id == "run:base:0")
    assert t.resources.needs == ("mock-solver",) and t.resources.est_seconds


def test_passive_result_passes_the_gate(fake_runner: None) -> None:
    b = _run(MockAdapter()).bundle
    assert b.status == "ok", b.summary_text
    net = b.items[0].data
    assert isinstance(net, NetworkResult)
    assert net.quality.verdict == "pass" and net.touchstone is not None
    assert net.touchstone.name.endswith(".s2p")
    assert any(f.code == "accuracy" and "3d_full_wave" in f.message for f in net.validity_flags)
    assert "PQM" in b.summary_text and "S21" in b.summary_text and "runtime" in b.summary_text
    ids = [i.id for i in b.items]
    assert {"sparams", "convergence", "sidecar", "mesh_preview"} <= set(ids)
    assert any(d.code == "EM.RUNTIME" for d in b.diagnostics)


def test_non_passive_output_is_failed_quality(fake_runner: None) -> None:
    b = _run(MockAdapter(gain=1.3)).bundle
    assert b.status == "failed_quality"
    assert b.items[0].data.quality.verdict == "fail"  # type: ignore[union-attr]


def test_fix_quality_repairs_passivity(fake_runner: None) -> None:
    cfg = {**CONFIG, "fix_quality": True}
    b = _run(MockAdapter(gain=1.05), cfg).bundle
    q = b.items[0].data.quality  # type: ignore[union-attr]
    assert any(x.startswith("passivity_enforced") for x in q.fixes_applied)
    assert q.max_singular_value <= 1.0 + 1e-3


def test_unconverged_energy_is_flagged(fake_runner: None) -> None:
    b = _run(MockAdapter(final_energy_db=-10.0)).bundle
    assert b.status == "warning"
    assert any(d.code == "EM.NOT_CONVERGED" for d in b.diagnostics)


def test_refinement_check_runs_the_fine_mesh(fake_runner: None) -> None:
    run = _run(MockAdapter(), {**CONFIG, "refinement_check": True})
    assert {"run:fine:0", "run:fine:1"} <= set(run.order)
    assert run.bundle.status == "ok"


def test_unknown_solver_is_a_setup_error() -> None:
    setup = AnalysisSetup(
        schema="oa-setup/1", name="t", analysis="em.sparams", config={**CONFIG, "solver": "nope"}
    )
    with pytest.raises(Exception, match=r"EM.NO_ADAPTER"):
        run_inline_full(EmSparams(), bld.microstrip_board(), setup, geometry=BoardGeometry.build)
