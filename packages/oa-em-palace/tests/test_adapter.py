# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Adapter contract without Palace: capabilities, check, prepare (meshing stubbed), command."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from palace_helpers import FIXTURES, GOLDEN, coarse

from oa_em_palace import PalaceAdapter
from oa_framework.em import SolverAdapter
from oa_framework.external import ToolInfo
from oa_model.em import EMProblem, Eigenmode, FarFieldRequest

TOOL = ToolInfo(program="palace", runner="wsl", path="/x/oa-palace-run")


def _fake_mesh(name: str) -> Any:
    def mesh(problem: EMProblem, path: Path) -> Any:
        path.write_text("$MeshFormat\n4.1 0 8\n$EndMeshFormat\n", encoding="utf-8")
        shutil.copy(GOLDEN / f"groups_{name}.json", path.with_suffix(".groups.json"))
        return SimpleNamespace(
            n_tets=230049,
            n_nodes=48017,
            n_edges=300000,
            n_faces=470000,
            h_min=1e-5,
            h_max=1e-3,
            min_sicn=0.31,
            content_hash="abc",
        )

    return mesh


def test_adapter_satisfies_the_protocol_and_declares_capabilities() -> None:
    ad = PalaceAdapter()
    assert isinstance(ad, SolverAdapter)
    assert ad.name == "palace" and ad.tool == "palace"
    caps = ad.capabilities()
    assert caps.wave_port_mode == "modal" and caps.excitations_per_run == "all"
    assert {"lumped", "wave"} <= caps.ports and caps.sheet_conductors
    assert caps.dispersive_dielectric == "single_frequency_fit" and not caps.farfield
    assert "absorbing" in caps.boundaries


def test_entry_point_registered_for_oa_em() -> None:
    from oa_em.adapters import load_adapter

    assert isinstance(load_adapter("palace"), PalaceAdapter)


def test_check_accepts_the_fixtures() -> None:
    for make in FIXTURES.values():
        errors = [d for d in PalaceAdapter().check(make()) if d.severity == "error"]
        assert not errors


def test_check_reports_unsupported_features() -> None:
    p = FIXTURES["microstrip_thru"]()
    bad = p.model_copy(
        update={
            "physics": Eigenmode(f_target=1e9),
            "outputs": p.outputs.model_copy(update={"farfield": FarFieldRequest(frequencies=(1e9,))}),
        }
    )
    codes = {d.code for d in PalaceAdapter().check(bad) if d.severity == "error"}
    assert {"PALACE.PHYSICS", "PALACE.FARFIELD"} <= codes
    silent = p.model_copy(update={"ports": tuple(q.model_copy(update={"excite": False}) for q in p.ports)})
    assert "PALACE.NO_EXCITATION" in {d.code for d in PalaceAdapter().check(silent)}


def test_check_warns_about_wave_port_z0() -> None:
    p = FIXTURES["stripline_thru"]()
    fixed = p.model_copy(update={"ports": tuple(q.model_copy(update={"z0": 50.0}) for q in p.ports)})
    assert "PALACE.WAVE_Z0_IGNORED" in {d.code for d in PalaceAdapter().check(fixed)}
    assert "PALACE.WAVE_Z0_IGNORED" not in {d.code for d in PalaceAdapter().check(p)}


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_prepare_writes_config_and_one_command(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("oa_em_palace.adapter._mesh", _fake_mesh(name))
    problem = coarse(FIXTURES[name]())
    run = PalaceAdapter(mpi_ranks=2, memory_gb=6).prepare(problem, tmp_path, TOOL)
    (cmd,) = run.commands  # Palace solves all excitations in one run
    assert cmd.program == "palace" and cmd.args == ("config.json",)
    assert cmd.progress_parser == "palace" and cmd.outputs == ("postpro/port-S.csv",)
    assert cmd.env == {
        "OA_PALACE_NP": "2",
        "OA_PALACE_MEM_GB": "6",
        "OA_PALACE_CPU_PERCENT": "200",
    }
    cfg = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert cfg == json.loads((GOLDEN / f"config_{name}.json").read_text(encoding="utf-8"))
    assert set(run.files) == {"config.json", "model.msh", "model.groups.json"}
    st = run.mesh_stats
    assert st["cells"] == 230049 and st["order"] == 2 and st["mpi_ranks"] == 2
    assert st["dofs"] == 300000 * 2 + 470000 * 2  # Nedelec p=2 on tets: 2 per edge + 2 per face
    assert {"est_memory_gb", "est_time_s", "dielectric_fit", "port_index"} <= set(st)


def test_prepare_is_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("oa_em_palace.adapter._mesh", _fake_mesh("microstrip_thru"))
    p = coarse(FIXTURES["microstrip_thru"]())
    a = PalaceAdapter().prepare(p, tmp_path / "a", TOOL)
    b = PalaceAdapter().prepare(p, tmp_path / "b", TOOL)
    assert (tmp_path / "a" / "config.json").read_bytes() == (tmp_path / "b" / "config.json").read_bytes()
    assert a.mesh_stats == b.mesh_stats


def test_prepare_refuses_a_problem_with_errors(tmp_path: Path) -> None:
    p = FIXTURES["microstrip_thru"]().model_copy(update={"physics": Eigenmode(f_target=1e9)})
    with pytest.raises(ValueError, match="not supported"):
        PalaceAdapter().prepare(p, tmp_path, TOOL)


def test_gpu_flag_and_ranks_hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("oa_em_palace.adapter._mesh", _fake_mesh("microstrip_thru"))
    p = coarse(FIXTURES["microstrip_thru"]())
    PalaceAdapter(gpu=True).prepare(p, tmp_path, TOOL)
    cfg = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert cfg["Solver"]["Device"] == "GPU"
    with pytest.raises(ValueError):
        PalaceAdapter(mpi_ranks=0)
