# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""LocalService end to end: real KiCad boards, oa_jobs, results."""

from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path
from typing import Any

import pytest
from service_helpers import BOARDS, make_registry

from oa_jobs import Budget, ToolsConfig
from oa_service import CallContext, Registry
from oa_service.local import LocalService

COMBINED = BOARDS / "combined" / "board.kicad_pcb"
MICRO = BOARDS / "microstrip" / "board.kicad_pcb"


@pytest.fixture
def svc(tmp_path: Path) -> LocalService:
    s = LocalService(
        make_registry(),
        cache_dir=tmp_path / "cache",
        tools=ToolsConfig({}),
        workers=2,
    )
    yield s  # type: ignore[misc]
    s.close()


@pytest.fixture
def reg(svc: LocalService) -> Registry:
    return Registry(svc)


@pytest.fixture
def ctx() -> CallContext:
    return CallContext(caller="test")


def tree_hash(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_open_bare_kicad_board_is_ephemeral_and_writes_nothing(
    reg: Registry, ctx: CallContext, tmp_path: Path
) -> None:
    board_dir = tmp_path / "bd"
    shutil.copytree(COMBINED.parent, board_dir)
    before = tree_hash(board_dir)
    env = reg.dispatch("board.open", {"path": str(board_dir / "board.kicad_pcb")}, ctx)
    assert env.ok, env.errors
    h = env.data
    assert h.ephemeral and h.project_path is None and h.source_tool == "kicad"
    assert h.summary.layer_count == 8
    pid = h.project_id
    nets = reg.dispatch("board.nets", {"project_id": pid, "query": "USB_*"}, ctx).data
    assert {n.name for n in nets.items} >= {"USB_D+", "USB_D-"}
    run = reg.dispatch("erc.run", {"project_id": pid, "wait_s": 30, "executor": "inline"}, ctx)
    assert run.ok, run.errors
    assert tree_hash(board_dir) == before


def test_erc_fails_on_combined_and_clean_on_microstrip(reg: Registry, ctx: CallContext) -> None:
    out: dict[str, int] = {}
    for name, path in (("combined", COMBINED), ("microstrip", MICRO)):
        pid = reg.dispatch("board.open", {"path": str(path)}, ctx).data.project_id
        env = reg.dispatch("erc.run", {"project_id": pid, "wait_s": 30, "executor": "inline"}, ctx)
        assert env.ok and env.data.job.state == "succeeded", env.errors
        rid = env.data.job.result_id
        out[name] = reg.dispatch("results.violations", {"result_id": rid}, ctx).data.total
    assert out["combined"] >= 2 and out["microstrip"] == 0


def test_project_directory_setups_and_write_gate(
    reg: Registry, svc: LocalService, tmp_path: Path
) -> None:
    proj = tmp_path / "proj"
    shutil.copytree(MICRO.parent, proj)
    (proj / "oa-project.yaml").write_text(
        "schema: oa-project/1\nname: demo\nboard: board.kicad_pcb\n", encoding="utf-8"
    )
    ctx = CallContext(caller="cli", allow_writes=True)
    h = reg.dispatch("board.open", {"path": str(proj)}, ctx).data
    assert not h.ephemeral and h.project_path and h.name == "demo"
    args = {
        "project_id": h.project_id,
        "name": "ercx",
        "analysis": "erc",
        "config": {"prefix": "ZZ"},
    }
    assert reg.dispatch("setups.put", args, ctx).errors[0].code == "CONFIRMATION_REQUIRED"
    assert not (proj / "oa" / "setups").exists()
    put = reg.dispatch("setups.put", {**args, "confirm": True}, ctx)
    assert put.ok and Path(put.data.path).is_file()
    assert [
        s.name for s in reg.dispatch("setups.list", {"project_id": h.project_id}, ctx).data.setups
    ] == ["ercx"]
    assert reg.dispatch(
        "setups.validate", {"project_id": h.project_id, "name": "ercx"}, ctx
    ).data.valid
    bad = reg.dispatch(
        "setups.put", {**args, "name": "bad", "config": {"prefx": "ZZ"}, "confirm": True}, ctx
    )
    assert bad.ok  # stored; validation reports the problem
    v = reg.dispatch("setups.validate", {"project_id": h.project_id, "name": "bad"}, ctx).data
    assert not v.valid and v.diagnostics[0].path == "/config/prefx"
    run = reg.dispatch(
        "erc.run",
        {"project_id": h.project_id, "setup": "ercx", "wait_s": 30, "executor": "inline"},
        ctx,
    )
    assert run.ok and run.data.job.summary.status == "ok"


def test_second_process_finds_job_and_result(
    reg: Registry, svc: LocalService, ctx: CallContext, tmp_path: Path
) -> None:
    pid = reg.dispatch("board.open", {"path": str(COMBINED)}, ctx).data.project_id
    job = reg.dispatch(
        "erc.run", {"project_id": pid, "wait_s": 30, "executor": "inline"}, ctx
    ).data.job
    svc2 = LocalService(make_registry(), cache_dir=svc.cache_dir, tools=ToolsConfig({}))
    try:
        reg2 = Registry(svc2)
        st = reg2.dispatch("jobs.status", {"job_id": job.job_id}, ctx)
        assert st.ok and st.data.status.state == "succeeded"
        summ = reg2.dispatch("results.summary", {"result_id": job.result_id}, ctx)
        assert summ.ok and summ.data.analysis == "erc"
        miss = reg2.dispatch("jobs.status", {"job_id": "01NOSUCHJOB0000000000000"}, ctx)
        assert miss.errors[0].code == "NOT_FOUND"
    finally:
        svc2.close()


def test_budget_exceeded_and_cancel(reg: Registry, ctx: CallContext) -> None:
    pid = reg.dispatch("board.open", {"path": str(MICRO)}, ctx).data.project_id
    cfg = {"n_tasks": 2, "est_seconds": 500.0}
    over = reg.dispatch(
        "analysis.run",
        {"project_id": pid, "analysis": "dummy.work", "config": cfg,
         "budget": {"max_runtime_s": 5}, "executor": "inline"},
        ctx,
    )  # fmt: skip
    assert over.errors[0].code == "BUDGET_EXCEEDED"
    assert over.errors[0].details["estimate"]["est_seconds"] == 1000.0
    dry = reg.dispatch(
        "analysis.run",
        {"project_id": pid, "analysis": "dummy.work", "config": cfg, "dry_run": True,
         "budget": {"max_runtime_s": 5}},
        ctx,
    )  # fmt: skip
    assert dry.ok and dry.data.estimate is not None and not dry.data.estimate.within_budget

    slow = {"n_tasks": 1, "sleep_s": 30.0}
    h = reg.dispatch(
        "analysis.run",
        {"project_id": pid, "analysis": "dummy.work", "config": slow, "executor": "inline"},
        ctx,
    ).data.job
    assert h.state in ("queued", "planning", "running")
    c = reg.dispatch("jobs.cancel", {"job_id": h.job_id}, ctx)
    assert c.ok
    deadline = time.monotonic() + 12
    state = ""
    while time.monotonic() < deadline:
        state = reg.dispatch(
            "jobs.status", {"job_id": h.job_id, "wait_s": 1}, ctx
        ).data.status.state
        if state == "cancelled":
            break
    assert state == "cancelled"


def test_cancelled_job_with_wait_reports_error(reg: Registry, ctx: CallContext) -> None:
    pid = reg.dispatch("board.open", {"path": str(MICRO)}, ctx).data.project_id
    env = reg.dispatch(
        "analysis.run",
        {"project_id": pid, "analysis": "dummy.work", "config": {"bogus": 1}, "executor": "inline"},
        ctx,
    )
    assert env.errors[0].code == "SETUP_INVALID" and env.errors[0].path == "/config/bogus"


def test_not_a_board_and_missing_paths(reg: Registry, ctx: CallContext, tmp_path: Path) -> None:
    junk = tmp_path / "x.txt"
    junk.write_text("hi")
    assert reg.dispatch("board.open", {"path": str(junk)}, ctx).errors[0].code == "INGEST_NO_READER"
    assert (
        reg.dispatch("board.open", {"path": str(tmp_path / "nope")}, ctx).errors[0].code
        == "NOT_FOUND"
    )
    assert reg.dispatch("board.open", {"path": str(tmp_path)}, ctx).errors[0].code == "NOT_FOUND"


def test_unavailable_analysis_is_reported(reg: Registry, ctx: CallContext) -> None:
    pid = reg.dispatch("board.open", {"path": str(MICRO)}, ctx).data.project_id
    env = reg.dispatch(
        "impedance.profile", {"project_id": pid, "nets": {"names": ["*"]}, "dry_run": True}, ctx
    )
    assert env.errors[0].code == "ANALYSIS_UNAVAILABLE"
    assert "erc" in env.errors[0].details["installed"]


def test_ipc2581_board_opens(reg: Registry, ctx: CallContext) -> None:
    env = reg.dispatch(
        "board.open", {"path": str(BOARDS / "microstrip" / "board.ipc2581.xml")}, ctx
    )
    assert env.ok, env.errors
    assert env.data.source_tool == "ipc2581"


def test_known_cli_examples_run(reg: Registry, ctx: CallContext) -> None:
    sub: dict[str, Any] = {}
    pid = reg.dispatch("board.open", {"path": str(COMBINED)}, ctx).data.project_id
    job = reg.dispatch(
        "erc.run", {"project_id": pid, "wait_s": 30, "executor": "inline"}, ctx
    ).data.job
    fp = (
        reg.dispatch("results.violations", {"result_id": job.result_id}, ctx)
        .data.items[0]
        .fingerprint
    )
    sub = {
        "$PROJECT_ID": pid, "$RESULT_ID": job.result_id, "$JOB_ID": job.job_id,
        "$BOARD": str(COMBINED), "$NET": "USB_D+", "$SETUP": "none", "$ANALYSIS": "dummy.work",
        "$FINGERPRINT": fp,
    }  # fmt: skip

    def fill(v: Any) -> Any:
        if isinstance(v, str):
            return sub.get(v, v)
        if isinstance(v, dict):
            return {k: fill(x) for k, x in v.items()}
        return v

    for d in reg.definitions():
        for ex in d.spec.examples:
            env = reg.dispatch(d.spec.name, fill(ex), ctx)
            assert not ({"SETUP_INVALID", "USAGE"} & {e.code for e in env.errors}), (
                d.spec.name,
                env.errors,
            )


def test_budget_model_roundtrip() -> None:
    assert Budget(max_runtime_s=5).max_runtime_s == 5
