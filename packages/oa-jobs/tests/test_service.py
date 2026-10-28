# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from oa_framework.jobs import AnalysisRequest, JobService
from oa_jobs import Budget, BudgetExceeded, JobNotFinished, LocalJobService, ToolsConfig
from oa_jobs.dummy import make_registry
from oa_model.errors import SetupError
from oa_results import ScalarSet

from .conftest import FACTORY, make_request

ReqFactory = Callable[..., AnalysisRequest]


def new_service(root: Path, **kw: Any) -> LocalJobService:
    opts: dict[str, Any] = {
        "registry_factory": FACTORY,
        "tools": ToolsConfig({}),
        "workers": 3,
        "grace_s": 3.0,
        "watchdog_interval": 0.05,
    }
    opts.update(kw)
    return LocalJobService(root, make_registry(), **opts)


def test_implements_job_service_protocol(service: LocalJobService) -> None:
    assert isinstance(service, JobService)


def test_inline_job_runs_reduces_and_stores_result(
    service: LocalJobService, req_factory: ReqFactory
) -> None:
    jid = service.submit(req_factory({"n_tasks": 5, "work": 100}))
    st = service.wait(jid, timeout=20)
    assert st.state == "succeeded"
    assert (st.tasks_total, st.tasks_done, st.tasks_cached, st.tasks_failed) == (5, 5, 0, 0)
    assert st.progress == 1.0
    bundle = service.result(jid)
    assert bundle.id == st.result_id
    scalars = bundle.items[0].data
    assert isinstance(scalars, ScalarSet)
    assert scalars.values[1].value == 5
    types = [e.type for e in service.events(jid)]
    assert types.count("task_started") == 5 and types.count("task_finished") == 5
    assert types[-1] == "state" and "result" in types
    assert [j.id for j in service.list(state="succeeded")] == [jid]


def test_provenance_populated_and_inputs_hash_stable_when_fully_cached(
    service: LocalJobService, req_factory: ReqFactory
) -> None:
    j1 = service.submit(req_factory({"n_tasks": 3}, caller="mcp"))
    s1 = service.wait(j1, 20)
    j2 = service.submit(req_factory({"n_tasks": 3}, caller="cli"))
    s2 = service.wait(j2, 20)
    assert s2.tasks_cached == 3  # 100 % cache hits
    b1, b2 = service.result(j1), service.result(j2)
    p1, p2 = b1.provenance, b2.provenance
    assert p1.inputs_hash and p1.inputs_hash == p2.inputs_hash
    assert p1.board_hash.startswith("sha256:") and p1.setup_hash.startswith("sha256:")
    assert p1.analysis == "dummy.work" and p1.finished_utc >= p1.started_utc
    ext1, ext2 = service.provenance_ext(s1.id), service.provenance_ext(s2.id)
    assert ext1["caller"] == "mcp" and ext2["caller"] == "cli"
    assert ext1["oa_version"]
    assert b1.id != b2.id
    # a changed payload is a different key
    j3 = service.submit(req_factory({"n_tasks": 3, "tag": "x"}))
    assert service.wait(j3, 20).tasks_cached == 0
    assert service.result(j3).provenance.inputs_hash != p1.inputs_hash
    cached_events = [e for e in service.events(j2) if e.type == "task_cached"]
    assert len(cached_events) == 3


def test_task_failure_fails_job_with_structured_error(
    service: LocalJobService, req_factory: ReqFactory, tmp_path: Path
) -> None:
    # a log path inside a missing directory makes run_task raise FileNotFoundError
    bad = tmp_path / "missing_dir" / "log.txt"
    jid = service.submit(req_factory({"n_tasks": 2, "log": str(bad)}))
    st = service.wait(jid, 20)
    assert st.state == "failed"
    assert st.error is not None and st.error.code == "INTERNAL_ERROR"
    assert st.tasks_failed == 1
    with pytest.raises(JobNotFinished):
        service.result(jid)


def test_dag_order_with_chain(service: LocalJobService, req_factory: ReqFactory) -> None:
    jid = service.submit(req_factory({"n_tasks": 4, "chain": True}, executor="inline"))
    service.wait(jid, 20)
    started = [e.task_id for e in service.events(jid) if e.type == "task_started"]
    assert started == ["t000", "t001", "t002", "t003"]


def test_unknown_setup_and_analysis_are_structured_errors(
    service: LocalJobService, project: Path
) -> None:
    from oa_framework.errors import UnknownPlugin
    from oa_model.setup import AnalysisSetup

    with pytest.raises(SetupError):
        service.submit(AnalysisRequest(project=project, setup="nope"))
    bad = AnalysisSetup(schema="oa-setup/1", name="s", analysis="nope.kind", config={})
    with pytest.raises(UnknownPlugin):
        service.submit(AnalysisRequest(project=project, setup="inline", inline_setup=bad))


def test_events_follow_streams_until_terminal(
    service: LocalJobService, req_factory: ReqFactory
) -> None:
    jid = service.submit(req_factory({"n_tasks": 3, "sleep_s": 0.05}))
    seqs = [e.seq for e in service.events(jid, follow=True)]
    assert seqs == list(range(1, len(seqs) + 1))
    assert service.status(jid).state == "succeeded"
    tail = list(service.events(jid, after_seq=seqs[-2]))
    assert [e.seq for e in tail] == [seqs[-1]]


def test_process_pool_20_tasks_restart_resumes_and_resubmit_is_all_cache(
    project: Path, tmp_path: Path
) -> None:
    log = tmp_path / "ran.log"
    cfg = {"n_tasks": 20, "sleep_s": 0.05, "work": 2000, "log": str(log)}
    svc1 = new_service(project.parent)
    svc1._stop_after = 8  # test hook: stop coordinating, as if the service had died
    jid = svc1.submit(make_request(project, cfg, executor="process"))
    deadline = time.monotonic() + 60
    while svc1.status(jid).tasks_done < 8 and time.monotonic() < deadline:
        time.sleep(0.05)
    svc1.close()
    done_before = len(log.read_text().splitlines())
    assert 8 <= done_before < 20

    svc2 = new_service(project.parent)  # restart: auto-resumes the unfinished job
    st = svc2.wait(jid, 60)
    assert st.state == "succeeded" and st.tasks_done == 20
    ran = sorted(int(x) for x in log.read_text().split())
    assert ran == list(range(20))  # every task ran exactly once across the restart

    jid2 = svc2.submit(make_request(project, cfg, executor="process"))
    st2 = svc2.wait(jid2, 60)
    assert st2.tasks_cached == 20
    assert len(log.read_text().splitlines()) == 20  # nothing re-executed
    assert svc2.result(jid).provenance.inputs_hash == svc2.result(jid2).provenance.inputs_hash
    svc2.close()


def test_running_tasks_requeued_on_restart(project: Path) -> None:
    svc1 = new_service(project.parent, auto_resume=False)
    jid = svc1.submit(make_request(project, {"n_tasks": 2, "sleep_s": 0.3}))
    svc1.close()  # stops the coordinator; leaves the job active
    svc1b = LocalJobService(project.parent, make_registry(), auto_resume=False)
    svc1b.store.update_task(jid, "t000", state="running", pid=99999)
    svc1b.close()
    svc2 = new_service(project.parent)
    assert svc2.wait(jid, 30).state == "succeeded"
    svc2.close()


def test_estimate_and_budget_exceeded_refused_before_any_task(
    service: LocalJobService, req_factory: ReqFactory, tmp_path: Path
) -> None:
    log = tmp_path / "never.log"
    cfg = {"n_tasks": 3, "est_seconds": 100.0, "log": str(log)}
    est = service.estimate(req_factory(cfg, budget=Budget(max_runtime_s=10)))
    assert est.est_seconds == 300.0 and est.tasks == 3 and not est.within_budget
    assert "max_runtime_s" in est.reasons[0]
    with pytest.raises(BudgetExceeded) as ei:
        service.submit(req_factory(cfg, budget=Budget(max_runtime_s=10)))
    assert ei.value.code == "BUDGET_EXCEEDED"
    assert ei.value.details["estimate"] == ei.value.estimate.model_dump(mode="json")
    assert not log.exists() and service.list() == []
    # memory: 3 tasks x 4 GB on 1 inline slot -> peak 4 GB
    est2 = service.estimate(
        req_factory({"n_tasks": 3, "memory_gb": 4.0}, budget=Budget(max_memory_gb=2))
    )
    assert est2.est_peak_memory_gb == 4.0 and not est2.within_budget
    ok = service.estimate(
        req_factory({"n_tasks": 3, "est_seconds": 1.0}, budget=Budget(max_runtime_s=10))
    )
    assert ok.within_budget and ok.warnings == ()


def test_ceiling_clamps_requested_budget(project: Path) -> None:
    svc = new_service(project.parent, ceiling=Budget(max_runtime_s=5))
    est = svc.estimate(
        make_request(project, {"est_seconds": 60.0}, budget=Budget(max_runtime_s=600))
    )
    assert est.budget.max_runtime_s == 5 and not est.within_budget
    svc.close()


def test_cancel_running_job(service: LocalJobService, req_factory: ReqFactory) -> None:
    jid = service.submit(req_factory({"n_tasks": 10, "sleep_s": 5.0}, executor="process"))
    deadline = time.monotonic() + 30
    while service.status(jid).state != "running" and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.5)
    t0 = time.monotonic()
    service.cancel(jid)
    st = service.wait(jid, 30)
    assert st.state == "cancelled" and st.cancel_reason == "user"
    assert time.monotonic() - t0 < 10


def test_max_runtime_budget_cancels_inline_job(
    service: LocalJobService, req_factory: ReqFactory
) -> None:
    jid = service.submit(
        req_factory(
            {"n_tasks": 4, "sleep_s": 30.0, "est_seconds": 0.1}, budget=Budget(max_runtime_s=0.6)
        )
    )
    t0 = time.monotonic()
    st = service.wait(jid, 30)
    assert st.state == "cancelled" and st.cancel_reason == "budget:max_runtime"
    assert st.error is not None and st.error.code == "BUDGET_EXCEEDED"
    assert 0.5 < time.monotonic() - t0 < 5
    assert st.budget_used["elapsed_s"] >= 0.5


@pytest.mark.slow
def test_max_runtime_5s_cancels_within_5_to_8_seconds(
    service: LocalJobService, req_factory: ReqFactory
) -> None:
    jid = service.submit(
        req_factory(
            {"n_tasks": 1, "sleep_s": 60.0, "est_seconds": 1.0}, budget=Budget(max_runtime_s=5)
        )
    )
    t0 = time.monotonic()
    st = service.wait(jid, 30)
    elapsed = time.monotonic() - t0
    assert st.state == "cancelled" and st.cancel_reason == "budget:max_runtime"
    assert 5 <= elapsed <= 8


def test_cancel_external_sleep_through_job_kills_child_fast(
    service: LocalJobService, req_factory: ReqFactory
) -> None:
    jid = service.submit(req_factory({"mode": "external_sleep"}, executor="process"))
    deadline = time.monotonic() + 30
    while service.status(jid).state != "running" and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(1.0)  # let the external process start
    t0 = time.monotonic()
    service.cancel(jid)
    st = service.wait(jid, 30)
    assert st.state == "cancelled"
    assert time.monotonic() - t0 < 12


def test_external_memory_over_budget_is_killed_with_reason(
    service: LocalJobService, req_factory: ReqFactory
) -> None:
    jid = service.submit(
        req_factory(
            {"mode": "external_alloc", "memory_gb": 0.05, "est_seconds": 1.0},
            budget=Budget(max_memory_gb=0.3),
        )
    )
    t0 = time.monotonic()
    st = service.wait(jid, 40)
    assert st.state == "cancelled" and st.cancel_reason == "budget:max_memory"
    assert st.error is not None and st.error.code == "BUDGET_EXCEEDED"
    assert time.monotonic() - t0 < 30


def test_external_not_allowed_by_budget(service: LocalJobService, req_factory: ReqFactory) -> None:
    jid = service.submit(
        req_factory({"mode": "external_sleep"}, budget=Budget(allow_external=False))
    )
    st = service.wait(jid, 20)
    assert st.state == "failed" and st.error is not None
    assert st.error.code == "BUDGET_EXCEEDED"


def test_cache_gc_keeps_results_readable(service: LocalJobService, req_factory: ReqFactory) -> None:
    jid = service.submit(req_factory({"n_tasks": 2}))
    service.wait(jid, 20)
    report = service.cache_gc(0)
    assert report.size_after >= 0
    assert service.result(jid).status == "ok"
