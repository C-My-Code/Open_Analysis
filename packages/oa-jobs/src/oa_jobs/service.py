# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``LocalJobService``: the ``oa_framework.jobs.JobService`` implementation (interfaces.md 4.4).

One coordinator thread per job plans nothing (planning happens in ``submit`` so a budget refusal
is immediate) but schedules the task DAG, consults the cache, dispatches tasks to the executor,
reduces, gates and stores the result. One watchdog thread enforces the runtime and memory
budgets once per ``watchdog_interval``. State lives in ``<root>/.oa/``::

    jobs.sqlite                job/task/event store (WAL)
    cache/cas, cache/tasks     content-addressed artifacts and task-output cache
    jobs/<id>/                 per-job directory: CANCEL flag, scratch/<task>/ working directories
    results/<result_id>/       written bundles (oa_results.io.write_bundle) and meta.json

Restart safety: ``LocalJobService(root)`` re-attaches to every unfinished job in the store. Tasks
that were ``running`` go back to ``pending``; finished tasks are not run again.
"""

from __future__ import annotations

import builtins
import concurrent.futures as cf
import contextlib
import functools
import json
import multiprocessing
import os
import queue
import re
import threading
import time
from collections.abc import Iterator, Mapping
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any

from oa_framework.analysis import Analysis, Plan, Task, TaskOutput
from oa_framework.errors import ErrorInfo, FrameworkError, to_error_info
from oa_framework.jobs import AnalysisRequest, JobEvent, JobState, JobStatus
from oa_framework.registry import Registry
from oa_framework.services import Services
from oa_jobs._util import canonical_json, load_callable, new_ulid, sha256_text, utc_now
from oa_jobs.cas import Cache, GcReport, task_key
from oa_jobs.execution import (
    ExecSpec,
    JobRunContext,
    cancel_reason,
    execute_task,
    request_cancel,
)
from oa_jobs.models import (
    Budget,
    BudgetExceeded,
    JobNotFinished,
    JobNotFound,
    JobRequest,
    JobStatusEx,
    JobTimeout,
    PlanEstimate,
    clamp_budget,
)
from oa_jobs.procs import kill_tree, tree_rss_bytes
from oa_jobs.store import TERMINAL_STATES, JobStore
from oa_jobs.tools import ToolsConfig
from oa_jobs.worker import run_task_in_worker
from oa_model.board import Board
from oa_model.errors import QualityGateMissing, SetupError
from oa_model.io import dumps_board, load_board
from oa_model.physics import PhysicsOverlay, apply_physics, load_physics
from oa_model.setup import (
    AnalysisSetup,
    DesignIntent,
    load_intent,
    load_project,
    load_setup,
    resolve_intent,
)
from oa_results.bundle import ResultBundle
from oa_results.io import read_bundle, write_bundle
from oa_results.network import NetworkResult

__all__ = ["LocalJobService", "estimate_plan"]

_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]+")
_REDUCE_ID = "__reduce__"


class _PlanCtx:
    """``PlanContext`` handed to ``validate_setup`` and ``plan``."""

    def __init__(
        self,
        board: Board,
        intent: Any,
        setup_hash: str,
        services: Services,
        geometry_factory: str | None,
    ) -> None:
        self.board = board
        self.intent = intent
        self.setup_hash = setup_hash
        self._services = services
        self._geometry_factory = geometry_factory
        self._geometry: Any = None

    def geometry(self) -> Any:
        if self._geometry is None:
            if not self._geometry_factory:
                raise NotImplementedError("no geometry_factory configured for the job service")
            self._geometry = load_callable(self._geometry_factory)(self.board)
        return self._geometry

    def services(self) -> Services:
        return self._services


@dataclass
class _Prep:
    req: JobRequest
    setup: AnalysisSetup
    board: Board
    raw_board: Board
    physics: PhysicsOverlay | None
    setup_hash: str
    analysis: Analysis
    plan: Plan
    order: list[str]
    budget: Budget
    estimate: PlanEstimate
    tools: ToolsConfig
    manifest_tools: dict[str, dict[str, str]]


@dataclass
class _Run:
    job_id: str
    spec: ExecSpec
    budget: Budget
    executor: str
    slots: int
    started_mono: float = field(default_factory=time.monotonic)
    base_elapsed: float = 0.0
    peak_rss: float = 0.0
    thread: threading.Thread | None = None
    last_flush: float = 0.0


def estimate_plan(plan: Plan, budget: Budget, slots: int) -> PlanEstimate:
    """Sum the task ``Resources`` (tasks without estimates count as 1 s and 1 GB)."""
    tasks = plan.tasks
    secs = [t.resources.est_seconds if t.resources.est_seconds is not None else 1.0 for t in tasks]
    mems = sorted((t.resources.memory_gb for t in tasks), reverse=True)
    unknown = sum(1 for t in tasks if t.resources.est_seconds is None)
    needs = tuple(sorted({n for t in tasks for n in t.resources.needs}))
    par = max(1, min(slots, len(tasks)))
    total = float(sum(secs))
    wall = max(max(secs, default=0.0), total / par)
    peak = float(sum(mems[:par]))
    cpus = min(slots, max(1, sum(t.resources.cpus for t in tasks)))
    reasons: list[str] = []
    if budget.max_runtime_s is not None and wall > budget.max_runtime_s:
        reasons.append(
            f"estimated wall time {wall:.3g} s exceeds max_runtime_s={budget.max_runtime_s:g}"
        )
    if budget.max_memory_gb is not None and peak > budget.max_memory_gb:
        reasons.append(
            f"estimated peak memory {peak:.3g} GB exceeds max_memory_gb={budget.max_memory_gb:g}"
        )
    if needs and not budget.allow_external:
        reasons.append(f"tasks need external tools {list(needs)} but allow_external=false")
    if needs and budget.allowed_tools is not None:
        names = {m.group(0) for n in needs if (m := _TOOL_NAME.match(n))}
        denied = sorted(names - set(budget.allowed_tools))
        if denied:
            reasons.append(f"tools {denied} are not in allowed_tools")
    warnings = (f"{unknown} task(s) without est_seconds counted as 1 s each",) if unknown else ()
    return PlanEstimate(
        tasks=len(tasks),
        est_seconds=total,
        est_wall_seconds=wall,
        est_peak_memory_gb=peak,
        cpus=cpus,
        needs=needs,
        external=bool(needs),
        within_budget=not reasons,
        budget=budget,
        reasons=tuple(reasons),
        warnings=warnings,
    )


def _enqueue(q: queue.Queue[Any], tid: str, fut: cf.Future[Any]) -> None:
    q.put((tid, fut))


def _check_plan(plan: Plan, kind: str) -> list[str]:
    ids = [t.id for t in plan.tasks]
    if len(set(ids)) != len(ids):
        raise FrameworkError("plan has duplicate task ids", code="PLAN_INVALID")
    known = set(ids)
    graph: dict[str, set[str]] = {}
    for t in plan.tasks:
        if t.analysis != kind:
            raise FrameworkError(
                f"task {t.id!r} belongs to {t.analysis!r}, not {kind!r}", code="PLAN_INVALID"
            )
        if t.id == _REDUCE_ID:
            raise FrameworkError(f"task id {_REDUCE_ID!r} is reserved", code="PLAN_INVALID")
        if missing := [d for d in t.depends_on if d not in known]:
            raise FrameworkError(f"task {t.id!r} depends on unknown {missing}", code="PLAN_INVALID")
        graph[t.id] = set(t.depends_on)
    if plan.reduce_inputs is not None and (
        missing := [i for i in plan.reduce_inputs if i not in known]
    ):
        raise FrameworkError(f"reduce_inputs refer to unknown tasks {missing}", code="PLAN_INVALID")
    try:
        return list(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        raise FrameworkError(
            f"plan has a dependency cycle: {exc.args[1]}", code="PLAN_INVALID"
        ) from exc


def _oa_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    for dist in ("oa-jobs", "oa-framework"):
        try:
            return version(dist)
        except PackageNotFoundError:
            continue
    return "0.0.0"


class LocalJobService:
    """Persistent, restart-safe job service for one project directory."""

    def __init__(
        self,
        root: str | Path,
        registry: Registry | None = None,
        *,
        registry_factory: str | None = None,
        geometry_factory: str | None = None,
        tools: ToolsConfig | None = None,
        workers: int | None = None,
        ceiling: Budget | None = None,
        auto_resume: bool = True,
        grace_s: float = 10.0,
        watchdog_interval: float = 0.25,
        poll_s: float = 0.05,
    ) -> None:
        self.root = Path(root)
        self.state_dir = self.root / ".oa"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.state_dir / "jobs.sqlite")
        self.cache = Cache(self.state_dir / "cache")
        self.results_dir = self.state_dir / "results"
        self.registry_factory = registry_factory
        self._registry = registry
        self.geometry_factory = geometry_factory
        self.tools = tools
        self.workers = workers or max(1, min((os.cpu_count() or 2) - 1, 8))
        self.ceiling = ceiling
        self.grace_s = grace_s
        self.watchdog_interval = watchdog_interval
        self.poll_s = poll_s
        self._pool: cf.ProcessPoolExecutor | None = None
        self._pool_lock = threading.Lock()
        self._runs: dict[str, _Run] = {}
        self._runs_lock = threading.Lock()
        self._closing = threading.Event()
        self._stop_after: int | None = None  # test hook: stop coordinating after N completions
        self._watchdog = threading.Thread(
            target=self._watchdog_loop, name="oa-watchdog", daemon=True
        )
        self._watchdog.start()
        if auto_resume:
            self.resume()

    # -- registry / tools ------------------------------------------------------------------

    @property
    def registry(self) -> Registry:
        if self._registry is None:
            self._registry = (
                load_callable(self.registry_factory)() if self.registry_factory else Registry.load()
            )
        return self._registry

    def _tools_for(self, manifest_tools: Mapping[str, Mapping[str, str]]) -> ToolsConfig:
        if self.tools is not None:
            return ToolsConfig(self.tools.tools, project_overrides=manifest_tools)
        return ToolsConfig(project_overrides=manifest_tools)

    # -- planning (submit / estimate) -----------------------------------------------------

    def _as_request(self, req: AnalysisRequest) -> JobRequest:
        return req if isinstance(req, JobRequest) else JobRequest.model_validate(req.model_dump())

    def _slots(self, req: JobRequest, budget: Budget) -> int:
        n = 1 if req.executor == "inline" else (req.max_workers or self.workers)
        if budget.max_cpus is not None:
            n = min(n, budget.max_cpus)
        return max(1, n)

    def _prepare(self, req0: AnalysisRequest) -> _Prep:
        req = self._as_request(req0)
        project = Path(req.project)
        manifest = load_project(project)
        pdir = project.parent
        if req.inline_setup is not None:
            setup = req.inline_setup
        else:
            for ext in (".yaml", ".yml"):
                cand = pdir / manifest.setups_dir / f"{req.setup}{ext}"
                if cand.is_file():
                    setup = load_setup(cand)
                    break
            else:
                raise SetupError(
                    f"setup {req.setup!r} not found under {pdir / manifest.setups_dir}"
                )
        board_path = pdir / manifest.board
        if board_path.suffix == ".oab" or board_path.name.endswith(".oab.json"):
            board = load_board(board_path)
        else:
            from oa_framework.ingest import ReadOptions, load

            board = load(
                board_path,
                ReadOptions.model_validate(manifest.read_options.model_dump()),
                registry=self.registry,
            )
        physics = load_physics(pdir / manifest.physics) if manifest.physics else None
        raw_board = board
        if physics is not None:
            board = apply_physics(board, physics)
        intent = (
            load_intent(pdir / manifest.intent) if manifest.intent is not None else DesignIntent()
        )
        resolved = resolve_intent(board, intent)
        setup_hash = "sha256:" + sha256_text(setup.model_dump_json())
        analysis = self.registry.analysis(setup.analysis)()
        cfg = analysis.Config.model_validate(setup.config)
        pctx = _PlanCtx(
            board, resolved, setup_hash, self.registry.services(), self.geometry_factory
        )
        diags = tuple(analysis.validate_setup(pctx, cfg))
        errors = [d for d in diags if d.severity == "error"]
        if errors:
            err = SetupError("; ".join(f"{d.code}: {d.message}" for d in errors), diags)
            raise err
        plan = analysis.plan(pctx, cfg)
        order = _check_plan(plan, analysis.kind)
        budget = clamp_budget(req.budget, self.ceiling)
        estimate = estimate_plan(plan, budget, self._slots(req, budget))
        return _Prep(
            req,
            setup,
            board,
            raw_board,
            physics,
            setup_hash,
            analysis,
            plan,
            order,
            budget,
            estimate,
            self._tools_for(manifest.tools),
            {k: dict(v) for k, v in manifest.tools.items()},
        )

    def estimate(self, req: AnalysisRequest) -> PlanEstimate:
        """Plan the job and sum its resources against the effective budget (no job is created)."""
        return self._prepare(req).estimate

    # -- JobService --------------------------------------------------------------------------

    def submit(self, req: AnalysisRequest) -> str:
        prep = self._prepare(req)
        if not prep.estimate.within_budget:
            raise BudgetExceeded(
                "Estimated resources exceed the budget: " + "; ".join(prep.estimate.reasons),
                prep.estimate,
            )
        job_id = new_ulid()
        kind = prep.analysis.kind
        board_text = dumps_board(prep.board)
        board_ref = self.cache.cas.put_bytes(
            board_text.encode("utf-8"), name="board.oab.json", media_type="application/json"
        )
        board_hash = prep.raw_board.content_hash()
        physics_hash = (
            "sha256:" + sha256_text(prep.physics.model_dump_json()) if prep.physics else None
        )
        tool_versions: dict[str, str | None] = {}
        for n in prep.estimate.needs:
            m = _TOOL_NAME.match(n)
            name = m.group(0) if m else n
            tool_versions[name] = prep.tools.version(name)
        rows = []
        for t in prep.plan.tasks:
            tv = {
                (m.group(0) if (m := _TOOL_NAME.match(n)) else n): tool_versions.get(
                    m.group(0) if m else n
                )
                for n in t.resources.needs
            }
            key = task_key(
                kind,
                prep.analysis.version,
                t.payload,
                [a.sha256 for a in t.inputs],
                board_hash,
                physics_hash,
                tv,
            )
            rows.append(
                (
                    t.id,
                    key,
                    t.model_dump_json(),
                    t.resources.est_seconds if t.resources.est_seconds is not None else 1.0,
                )
            )
        meta = {
            "board_sha": board_ref.sha256,
            "board_hash": board_hash,
            "physics_hash": physics_hash,
            "setup_hash": prep.setup_hash,
            "reduce_inputs": list(prep.plan.reduce_inputs)
            if prep.plan.reduce_inputs is not None
            else None,
            "project": str(Path(prep.req.project)),
            "tools": prep.tools.tools,
            "tool_versions": tool_versions,
            "budget": prep.budget.model_dump(mode="json"),
            "estimate": prep.estimate.model_dump(mode="json"),
            "executor": prep.req.executor,
            "slots": self._slots(prep.req, prep.budget),
            "keep_scratch": prep.req.keep_scratch,
            "registry_factory": self.registry_factory,
            "geometry_factory": self.geometry_factory,
            "oa_version": _oa_version(),
        }
        self.store.create_job(
            job_id=job_id,
            analysis=kind,
            setup=prep.setup.name,
            priority=prep.req.priority,
            request=json.loads(prep.req.model_dump_json()),
            setup_doc=json.loads(prep.setup.model_dump_json()),
            meta=meta,
            caller=prep.req.caller,
            tasks=rows,
        )
        self._emit(job_id, "state", {"state": "queued", "tasks": len(rows)})
        self._start(job_id)
        return job_id

    def status(self, job_id: str) -> JobStatusEx:
        row = self.store.job(job_id)
        if row is None:
            raise JobNotFound(f"No job {job_id!r}", path=job_id, hint="List jobs with list().")
        return self._status_of(row)

    def list(self, *, state: JobState | None = None, limit: int = 100) -> builtins.list[JobStatus]:
        return [self._status_of(r) for r in self.store.jobs(state, limit)]

    def cancel(self, job_id: str, reason: str = "user") -> None:
        row = self.store.job(job_id)
        if row is None:
            raise JobNotFound(f"No job {job_id!r}", path=job_id)
        if row["state"] in TERMINAL_STATES:
            return
        request_cancel(self.state_dir / "jobs" / job_id, reason)

    def events(
        self, job_id: str, after_seq: int = 0, *, follow: bool = False
    ) -> Iterator[JobEvent]:
        if self.store.job(job_id) is None:
            raise JobNotFound(f"No job {job_id!r}", path=job_id)
        seq = after_seq
        while True:
            rows = self.store.events(job_id, seq, 1000)
            for r in rows:
                seq = int(r["seq"])
                yield JobEvent(
                    seq=seq,
                    job_id=job_id,
                    type=r["type"],
                    task_id=r["task_id"],
                    data=json.loads(r["data_json"]),
                    utc=r["utc"],
                )
            if rows:
                continue
            if not follow:
                return
            job = self.store.job(job_id)
            if job is None or job["state"] in TERMINAL_STATES:
                if not self.store.events(job_id, seq, 1):
                    return
                continue
            self.store.wait_for_change(0.05)

    def result(self, job_id: str) -> ResultBundle:
        st = self.status(job_id)
        if st.state != "succeeded" or st.result_id is None:
            raise JobNotFinished(f"Job {job_id} is {st.state}, it has no result", st.error)
        return read_bundle(self.results_dir / st.result_id)

    def provenance_ext(self, job_id: str) -> dict[str, Any]:
        """``oa_version``, ``tool_versions`` and ``caller`` of a finished job (contract A6)."""
        st = self.status(job_id)
        if st.result_id is None:
            raise JobNotFinished(f"Job {job_id} has no result yet")
        doc: dict[str, Any] = json.loads(
            (self.results_dir / st.result_id / "meta.json").read_text(encoding="utf-8")
        )
        return doc

    def wait(self, job_id: str, timeout: float | None = None) -> JobStatusEx:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            st = self.status(job_id)
            if st.state in TERMINAL_STATES:
                return st
            if deadline is not None and time.monotonic() >= deadline:
                raise JobTimeout(
                    f"Job {job_id} is still {st.state} after {timeout:g} s",
                    hint="Poll status() or wait longer.",
                )
            self.store.wait_for_change(0.05)

    # -- cache -----------------------------------------------------------------------------

    def cache_gc(self, max_size_bytes: int) -> GcReport:
        """LRU eviction (``oa cache gc --max-size``). Results' and active jobs' blobs are kept."""
        pinned: set[str] = set()
        for r in self.store.jobs(None, 100000):
            meta = json.loads(r["meta_json"])
            if r["state"] not in TERMINAL_STATES:
                pinned.add(meta["board_sha"])
        for f in self.results_dir.glob("*/result.json") if self.results_dir.is_dir() else ():
            pinned.update(re.findall(r"[0-9a-f]{64}", f.read_text(encoding="utf-8")))
        return self.cache.gc(max_size_bytes, pinned)

    # -- lifecycle -------------------------------------------------------------------------

    def resume(self) -> builtins.list[str]:
        """Re-attach to unfinished jobs of a previous service instance."""
        resumed = []
        for jid in self.store.active_job_ids():
            with self._runs_lock:
                if jid in self._runs:
                    continue
            self.store.requeue_running(jid)
            self._start(jid)
            resumed.append(jid)
        return resumed

    def close(self) -> None:
        """Stop coordinating (jobs stay resumable), stop the pool, close the store."""
        self._closing.set()
        with self._runs_lock:
            threads = [r.thread for r in self._runs.values() if r.thread is not None]
        for t in threads:
            t.join(timeout=30)
        self._watchdog.join(timeout=5)
        with self._pool_lock:
            if self._pool is not None:
                self._pool.shutdown(wait=True, cancel_futures=True)
                self._pool = None
        self.store.close()

    def __enter__(self) -> LocalJobService:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- internals -------------------------------------------------------------------------

    def _emit(
        self, job_id: str, typ: str, data: dict[str, Any], task_id: str | None = None
    ) -> None:
        self.store.append_event(job_id, typ, data, task_id)

    def _spec_for(self, job_id: str, meta: Mapping[str, Any], inline: bool) -> ExecSpec:
        return ExecSpec(
            state_dir=str(self.state_dir),
            job_id=job_id,
            board_sha=meta["board_sha"],
            tools=meta["tools"],
            registry_factory=meta.get("registry_factory"),
            geometry_factory=meta.get("geometry_factory"),
            budget=meta["budget"],
            keep_scratch=bool(meta["keep_scratch"]),
            grace_s=self.grace_s,
            poll_s=self.poll_s,
            inline=inline,
        )

    def _start(self, job_id: str) -> None:
        row = self.store.job(job_id)
        assert row is not None
        meta = json.loads(row["meta_json"])
        inline = meta["executor"] == "inline"
        run = _Run(
            job_id=job_id,
            spec=self._spec_for(job_id, meta, inline),
            budget=Budget.model_validate(meta["budget"]),
            executor=meta["executor"],
            slots=int(meta["slots"]),
            base_elapsed=float(row["elapsed_s"]),
        )
        run.thread = threading.Thread(
            target=self._coordinate, args=(run,), name=f"oa-job-{job_id[-6:]}", daemon=True
        )
        with self._runs_lock:
            self._runs[job_id] = run
        run.thread.start()

    def _pool_get(self) -> cf.ProcessPoolExecutor:
        with self._pool_lock:
            if self._pool is None:
                self._pool = cf.ProcessPoolExecutor(
                    max_workers=self.workers, mp_context=multiprocessing.get_context("spawn")
                )
            return self._pool

    def _pool_reset(self) -> None:
        with self._pool_lock:
            if self._pool is not None:
                self._pool.shutdown(wait=False, cancel_futures=True)
                self._pool = None

    def _status_of(self, row: Any) -> JobStatusEx:
        jid = row["id"]
        c = self.store.counts(jid)
        state = row["state"]
        if state == "succeeded":
            progress = 1.0
        elif c["est_total"] > 0:
            progress = min(1.0, c["est_done"] / c["est_total"])
        else:
            progress = 0.0
        with self._runs_lock:
            run = self._runs.get(jid)
        elapsed = float(row["elapsed_s"])
        if run is not None and state not in TERMINAL_STATES:
            elapsed = run.base_elapsed + time.monotonic() - run.started_mono
        err = ErrorInfo.model_validate_json(row["error_json"]) if row["error_json"] else None
        return JobStatusEx(
            id=jid,
            state=state,
            analysis=row["analysis"],
            setup=row["setup"],
            progress=progress,
            tasks_total=int(c["total"]),
            tasks_done=int(c["done"]),
            tasks_cached=int(c["cached"]),
            tasks_failed=int(c["failed"]),
            message=row["message"],
            result_id=row["result_id"],
            error=err,
            created_utc=row["created_utc"],
            updated_utc=row["updated_utc"],
            cancel_reason=row["cancel_reason"],
            budget_used={
                "elapsed_s": round(elapsed, 3),
                "peak_memory_gb": round(float(row["peak_memory_gb"]), 4),
            },
        )

    def _set_state(self, job_id: str, state: str, **fields: Any) -> None:
        # event first: a follower that sees the terminal state must already see its event
        self._emit(job_id, "state", {"state": state})
        with self._runs_lock:
            run = self._runs.get(job_id)
        if state in TERMINAL_STATES and run is not None:
            fields["elapsed_s"] = run.base_elapsed + time.monotonic() - run.started_mono
            fields["peak_memory_gb"] = run.peak_rss / 1e9
        self.store.update_job(job_id, state=state, **fields)

    # -- watchdog --------------------------------------------------------------------------

    def _watchdog_loop(self) -> None:
        while not self._closing.wait(self.watchdog_interval):
            with self._runs_lock:
                runs = [r for r in self._runs.values() if r.thread and r.thread.is_alive()]
            for run in runs:
                try:
                    self._watch(run)
                except Exception:  # never let the watchdog die
                    continue

    def _watch(self, run: _Run) -> None:
        job_dir = run.spec.job_dir
        elapsed = run.base_elapsed + time.monotonic() - run.started_mono
        b = run.budget
        if b.max_runtime_s is not None and elapsed > b.max_runtime_s:
            request_cancel(job_dir, "budget:max_runtime")
        pids = self.store.running_pids(run.job_id)
        rss = sum(tree_rss_bytes(p) for p in pids)
        run.peak_rss = max(run.peak_rss, float(rss))
        if b.max_memory_gb is not None and rss > b.max_memory_gb * 1e9:
            request_cancel(job_dir, "budget:max_memory")
        now = time.monotonic()
        if now - run.last_flush > 1.0:
            run.last_flush = now
            self.store.update_job(
                run.job_id, elapsed_s=elapsed, peak_memory_gb=max(run.peak_rss / 1e9, 0.0)
            )

    # -- coordinator -----------------------------------------------------------------------

    def _coordinate(self, run: _Run) -> None:
        job_id = run.job_id
        try:
            self._execute(run)
        except BaseException as exc:
            if self._closing.is_set():
                return
            with contextlib.suppress(Exception):
                self._set_state(job_id, "failed", error_json=to_error_info(exc).model_dump_json())
        finally:
            try:
                row = self.store.job(job_id)
                if row is not None and row["state"] in TERMINAL_STATES:
                    self.store.update_job(
                        job_id,
                        elapsed_s=run.base_elapsed + time.monotonic() - run.started_mono,
                        peak_memory_gb=run.peak_rss / 1e9,
                    )
            except Exception:
                pass

    def _execute(self, run: _Run) -> None:
        job_id = run.job_id
        store = self.store
        row = store.job(job_id)
        assert row is not None
        meta = json.loads(row["meta_json"])
        setup = AnalysisSetup.model_validate_json(row["setup_json"])
        registry = self.registry
        analysis = registry.analysis(setup.analysis)()
        inline = run.executor == "inline"
        self._set_state(job_id, "running", message="")
        trows = store.tasks(job_id)
        tasks = {r["task_id"]: Task.model_validate_json(r["task_json"]) for r in trows}
        keys = {r["task_id"]: r["key"] for r in trows}
        state = {r["task_id"]: r["state"] for r in trows}
        outputs: dict[str, TaskOutput] = {}
        for r in trows:
            if r["state"] in ("succeeded", "cached") and r["output_json"]:
                outputs[r["task_id"]] = TaskOutput.model_validate_json(r["output_json"])
            elif r["state"] in ("succeeded", "cached"):
                state[r["task_id"]] = "pending"  # lost output: run again
        order = list(tasks)
        inflight: dict[str, cf.Future[dict[str, Any]] | None] = {}
        done_q: queue.Queue[tuple[str, cf.Future[dict[str, Any]]]] = queue.Queue()
        failure: ErrorInfo | None = None
        stop_reason: str | None = None
        stop_since: float | None = None
        completions = 0
        cpu_used = 0
        mem_used = 0.0
        job_dir = run.spec.job_dir
        job_dir.mkdir(parents=True, exist_ok=True)

        def ready_ids() -> builtins.list[str]:
            ids = [
                t
                for t in order
                if state[t] == "pending"
                and all(state[d] in ("succeeded", "cached") for d in tasks[t].depends_on)
            ]
            ids.sort(key=lambda t: -(tasks[t].resources.est_seconds or 1.0))
            return ids

        def mark_done(tid: str, out: TaskOutput, *, cached: bool) -> None:
            state[tid] = "cached" if cached else "succeeded"
            outputs[tid] = out
            store.update_task(
                job_id,
                tid,
                state=state[tid],
                cached=int(cached),
                output_json=out.model_dump_json(),
                finished_utc=utc_now(),
                pid=None,
            )
            self._emit(
                job_id,
                "task_cached" if cached else "task_finished",
                {"key": keys[tid][:16], "artifacts": len(out.artifacts)},
                tid,
            )
            for d in out.diagnostics:
                self._emit(job_id, "diagnostic", d.model_dump(mode="json"), tid)

        def handle(tid: str, res: dict[str, Any] | BaseException) -> None:
            nonlocal cpu_used, mem_used, failure, stop_reason, completions
            t = tasks[tid]
            cpu_used -= min(t.resources.cpus, run.slots)
            mem_used -= t.resources.memory_gb
            inflight.pop(tid, None)
            completions += 1
            if isinstance(res, BrokenProcessPool | cf.CancelledError):
                self._pool_reset()
                if cancel_reason(job_dir) is not None:
                    res = {"cancelled": cancel_reason(job_dir) or "user"}
                else:
                    attempts = int(
                        next(r["attempts"] for r in store.tasks(job_id) if r["task_id"] == tid)
                    )
                    if attempts < 2:
                        store.update_task(
                            job_id, tid, state="pending", attempts=attempts + 1, pid=None
                        )
                        state[tid] = "pending"
                        return
                    res = {
                        "error": ErrorInfo(
                            code="WORKER_CRASHED", message=f"worker running {tid!r} died repeatedly"
                        ).model_dump(mode="json")
                    }
            elif isinstance(res, BaseException):
                res = {"error": to_error_info(res).model_dump(mode="json")}
            if "output" in res:
                out = TaskOutput.model_validate_json(res["output"])
                if t.cache:
                    self.cache.tasks.put(keys[tid], out)
                mark_done(tid, out, cached=False)
            elif "cancelled" in res:
                state[tid] = "pending"
                store.update_task(job_id, tid, state="pending", pid=None)
                stop_reason = stop_reason or str(res["cancelled"])
            else:
                info = ErrorInfo.model_validate(res["error"])
                state[tid] = "failed"
                store.update_task(
                    job_id,
                    tid,
                    state="failed",
                    error_json=info.model_dump_json(),
                    finished_utc=utc_now(),
                    pid=None,
                )
                self._emit(
                    job_id, "diagnostic", {"severity": "error", **info.model_dump(mode="json")}, tid
                )
                failure = failure or info

        while True:
            halt = self._closing.is_set() or (
                self._stop_after is not None and completions >= self._stop_after
            )
            if halt and not inflight:
                return  # leave the store as is: the job resumes with the next service
            cancelled = cancel_reason(job_dir)
            stopping = cancelled is not None or failure is not None or halt
            if cancelled is not None and stop_since is None:
                stop_since = time.monotonic()
            did_work = False
            progressed = True
            while progressed and not stopping:
                progressed = False
                for tid in ready_ids():
                    t = tasks[tid]
                    if t.cache:
                        hit = self.cache.tasks.get(keys[tid], self.cache.cas)
                        if hit is not None:
                            mark_done(tid, hit.model_copy(update={"task_id": tid}), cached=True)
                            progressed = did_work = True
                            continue
                    need = min(t.resources.cpus, run.slots)
                    if inflight and cpu_used + need > run.slots:
                        continue
                    lim = run.budget.max_memory_gb
                    if inflight and lim is not None and mem_used + t.resources.memory_gb > lim:
                        continue
                    state[tid] = "running"
                    store.update_task(job_id, tid, state="running", started_utc=utc_now())
                    self._emit(job_id, "task_started", {"key": keys[tid][:16]}, tid)
                    cpu_used += need
                    mem_used += t.resources.memory_gb
                    did_work = True
                    if inline:
                        inflight[tid] = None
                        handle(tid, execute_task(run.spec, t.model_dump_json(), registry))
                        break  # re-evaluate cancel/stop conditions after every inline task
                    fut = self._pool_get().submit(run_task_in_worker, run.spec, t.model_dump_json())
                    inflight[tid] = fut
                    fut.add_done_callback(functools.partial(_enqueue, done_q, tid))
                if inline:
                    break
            if not inflight and (
                stopping or all(s in ("succeeded", "cached") for s in state.values())
            ):
                break
            if not inflight and not ready_ids():
                break  # nothing can run (failed dependency chain)
            if inline and did_work:
                continue
            try:
                done_tid, done_fut = done_q.get(timeout=0.05)
            except queue.Empty:
                pass
            else:
                res2: dict[str, Any] | BaseException
                try:
                    res2 = done_fut.result()
                except BaseException as exc:
                    res2 = exc
                handle(done_tid, res2)
            if stop_since is not None and inflight and time.monotonic() - stop_since > self.grace_s:
                for pid in store.running_pids(job_id):
                    kill_tree(pid)  # grace over: hard-stop workers that ignore cancellation
                stop_since = time.monotonic()

        # -- finalize -----------------------------------------------------------------------
        reason = cancel_reason(job_dir)
        if failure is not None:
            self._set_state(
                job_id, "failed", error_json=failure.model_dump_json(), message=failure.message
            )
            return
        if reason is not None:
            code = "BUDGET_EXCEEDED" if reason.startswith("budget:") else "CANCELLED"
            info = ErrorInfo(
                code=code,
                message=(
                    f"Job stopped: {reason.split(':', 1)[1].replace('_', ' ')} budget exceeded"
                    if code == "BUDGET_EXCEEDED"
                    else "Job cancelled"
                ),
            )
            self._set_state(
                job_id,
                "cancelled",
                cancel_reason=reason,
                error_json=info.model_dump_json(),
                message=info.message,
            )
            return
        if not all(s in ("succeeded", "cached") for s in state.values()):
            raise FrameworkError("job cannot finish: unresolved tasks remain", code="JOB_STUCK")
        self._reduce(run, meta, setup, analysis, tasks, keys, outputs)

    def _reduce(
        self,
        run: _Run,
        meta: Mapping[str, Any],
        setup: AnalysisSetup,
        analysis: Analysis,
        tasks: Mapping[str, Task],
        keys: Mapping[str, str],
        outputs: Mapping[str, TaskOutput],
    ) -> None:
        job_id = run.job_id
        self._set_state(job_id, "reducing")
        started = self.store.job(job_id)
        assert started is not None
        cfg = analysis.Config.model_validate(setup.config)
        want = meta["reduce_inputs"]
        sel = list(tasks) if want is None else [t for t in tasks if t in set(want)]
        scratch = run.spec.job_dir / "scratch" / _REDUCE_ID
        scratch.mkdir(parents=True, exist_ok=True)
        rtask = Task(id=_REDUCE_ID, analysis=analysis.kind, payload={}, cache=False)
        ctx = JobRunContext(run.spec, rtask, scratch, registry=self.registry, store=self.store)
        bundle = analysis.reduce(ctx, cfg, {t: outputs[t] for t in sel})
        for item in bundle.items:
            if isinstance(item.data, NetworkResult) and getattr(item.data, "quality", None) is None:
                raise QualityGateMissing(
                    f"network item {item.id!r} has no QualityReport; run oa_network.quality.assess"
                )
        result_id = new_ulid()
        inputs_hash = sha256_text(
            canonical_json(
                [
                    sorted(keys[t] for t in tasks),
                    meta["board_hash"],
                    meta["physics_hash"],
                    meta["setup_hash"],
                ]
            )
        )
        prov = bundle.provenance.model_copy(
            update={
                "board_hash": meta["board_hash"],
                "physics_hash": meta["physics_hash"],
                "setup_hash": meta["setup_hash"],
                "inputs_hash": inputs_hash,
                "started_utc": started["created_utc"],
                "finished_utc": utc_now(),
                "host": bundle.provenance.host or _hostname(),
            }
        )
        bundle = bundle.model_copy(update={"id": result_id, "provenance": prov})
        out_dir = self.results_dir / result_id
        write_bundle(bundle, out_dir, artifacts=self.cache.cas.path)
        (out_dir / "meta.json").write_text(
            json.dumps(
                {
                    "oa_version": meta["oa_version"],
                    "tool_versions": {k: v for k, v in meta["tool_versions"].items() if v},
                    "caller": started["caller"],
                    "inputs_hash": inputs_hash,
                },
                indent=1,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        if not run.spec.keep_scratch:
            import shutil

            shutil.rmtree(run.spec.job_dir / "scratch", ignore_errors=True)
        self._emit(job_id, "result", {"result_id": result_id, "status": bundle.status})
        self._set_state(job_id, "succeeded", result_id=result_id, message=bundle.summary_text)


def _hostname() -> str:
    import socket

    return socket.gethostname()
