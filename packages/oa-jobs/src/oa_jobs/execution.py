# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Task execution: the ``RunContext`` implementation and the function workers run.

``execute_task`` is shared by the ``inline`` executor (called in the coordinator thread) and the
``process`` executor (called in a spawned worker via :func:`oa_jobs.worker.run_task_in_worker`).
It never raises: the outcome is a small JSON-able dict so nothing exotic has to be pickled.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oa_framework.analysis import Task, TaskOutput
from oa_framework.errors import FrameworkError, to_error_info
from oa_framework.external import ExternalCommand, ExternalResult
from oa_framework.registry import Registry
from oa_framework.services import Services
from oa_jobs._util import load_callable
from oa_jobs.cas import CasStore
from oa_jobs.models import Budget, BudgetBreach
from oa_jobs.runners import make_runner
from oa_jobs.store import JobStore
from oa_jobs.tools import ToolsConfig
from oa_model.board import Board
from oa_model.errors import Cancelled, ExternalToolFailed
from oa_model.io import loads_board
from oa_results.artifact import ArtifactRef

__all__ = [
    "CANCEL_FILE",
    "ExecSpec",
    "JobRunContext",
    "cancel_reason",
    "execute_task",
    "request_cancel",
]

CANCEL_FILE = "CANCEL"
_PROGRESS_MIN_INTERVAL_S = 0.2


@dataclass(frozen=True)
class ExecSpec:
    """Everything a worker needs to run tasks of one job (picklable)."""

    state_dir: str
    job_id: str
    board_sha: str
    tools: dict[str, dict[str, str]]
    registry_factory: str | None = None
    geometry_factory: str | None = None
    budget: dict[str, Any] | None = None
    keep_scratch: bool = False
    threads: int = 1
    grace_s: float = 10.0
    poll_s: float = 0.05
    inline: bool = False  # inline tasks share the service process: no per-task pid

    @property
    def job_dir(self) -> Path:
        return Path(self.state_dir) / "jobs" / self.job_id


def request_cancel(job_dir: Path, reason: str = "user") -> None:
    """Create the cancel flag (the first reason wins)."""
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        with open(job_dir / CANCEL_FILE, "x", encoding="utf-8") as fh:
            fh.write(reason)
    except FileExistsError:
        pass


def cancel_reason(job_dir: Path) -> str | None:
    p = job_dir / CANCEL_FILE
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8").strip() or "user"
    except OSError:
        return "user"


class _FileCancel:
    def __init__(self, job_dir: Path) -> None:
        self.job_dir = job_dir

    def is_cancelled(self) -> bool:
        return (self.job_dir / CANCEL_FILE).exists()


# Per-process caches (workers are long-lived).
_stores: dict[str, JobStore] = {}
_registries: dict[str, Registry] = {}
_boards: dict[str, Board] = {}
_geometries: dict[tuple[str, str], Any] = {}
_cache_lock = threading.Lock()


def _store(state_dir: str) -> JobStore:
    with _cache_lock:
        s = _stores.get(state_dir)
        if s is None:
            s = _stores[state_dir] = JobStore(Path(state_dir) / "jobs.sqlite")
        return s


def _registry(spec: ExecSpec) -> Registry:
    key = spec.registry_factory or ""
    with _cache_lock:
        reg = _registries.get(key)
        if reg is None:
            reg = load_callable(key)() if key else Registry.load()
            _registries[key] = reg
        return reg


class JobRunContext:
    """``oa_framework.analysis.RunContext`` for one task (or the reduce step) of a job."""

    def __init__(
        self,
        spec: ExecSpec,
        task: Task,
        scratch: Path,
        *,
        registry: Registry | None = None,
        store: JobStore | None = None,
    ) -> None:
        self.spec = spec
        self.task = task
        self.scratch = scratch
        self.threads = spec.threads
        self._registry_obj = registry
        self._store_obj = store
        self._cas = CasStore(Path(spec.state_dir) / "cache" / "cas")
        self._cancel = _FileCancel(spec.job_dir)
        self._last_progress = 0.0
        self._runners: dict[str, Any] = {}

    # -- lazily resolved collaborators ---------------------------------------------------

    @property
    def store(self) -> JobStore:
        return self._store_obj or _store(self.spec.state_dir)

    @property
    def registry(self) -> Registry:
        return self._registry_obj or _registry(self.spec)

    @property
    def budget(self) -> Budget:
        return Budget.model_validate(self.spec.budget or {})

    # -- RunContext ----------------------------------------------------------------------

    def board(self) -> Board:
        sha = self.spec.board_sha
        with _cache_lock:
            b = _boards.get(sha)
        if b is None:
            path = self._cas.blob_path(sha)
            b = loads_board(path.read_text(encoding="utf-8"))
            with _cache_lock:
                _boards[sha] = b
                while len(_boards) > 4:
                    _boards.pop(next(iter(_boards)))
        return b

    def geometry(self) -> Any:
        if not self.spec.geometry_factory:
            raise NotImplementedError(
                "no geometry factory configured; pass geometry_factory='module:callable' to the "
                "job service (oa_geometry is not a dependency of oa_jobs)"
            )
        key = (self.spec.board_sha, self.spec.geometry_factory)
        with _cache_lock:
            g = _geometries.get(key)
        if g is None:
            g = load_callable(self.spec.geometry_factory)(self.board())
            with _cache_lock:
                _geometries[key] = g
        return g

    def services(self) -> Services:
        return self.registry.services()

    def progress(self, fraction: float, message: str = "") -> None:
        if not 0.0 <= fraction <= 1.0:
            raise ValueError(f"progress fraction must be in [0, 1], got {fraction}")
        now = time.monotonic()
        if fraction < 1.0 and now - self._last_progress < _PROGRESS_MIN_INTERVAL_S:
            return
        self._last_progress = now
        self.store.append_event(
            self.spec.job_id,
            "progress",
            {"fraction": fraction, "message": message},
            task_id=self.task.id,
        )

    def check_cancelled(self) -> None:
        if self._cancel.is_cancelled():
            raise Cancelled(cancel_reason(self.spec.job_dir) or "cancelled")

    def log(self, level: str, message: str, **fields: object) -> None:
        data: dict[str, Any] = {"level": level, "message": message}
        data.update(
            {
                k: v if isinstance(v, str | int | float | bool | None) else repr(v)
                for k, v in fields.items()
            }
        )
        self.store.append_event(self.spec.job_id, "log", data, task_id=self.task.id)

    def artifacts(self) -> CasStore:
        return self._cas

    def run_external(self, cmd: ExternalCommand) -> ExternalResult:
        b = self.budget
        if not b.allow_external:
            raise FrameworkError(
                f"External tool {cmd.program!r} is not allowed by the job budget",
                code="BUDGET_EXCEEDED",
                path="budget.allow_external",
                hint="Run with allow_external=true.",
            )
        if b.allowed_tools is not None and cmd.program not in b.allowed_tools:
            raise FrameworkError(
                f"Tool {cmd.program!r} is not in the allowed tools {list(b.allowed_tools)}",
                code="BUDGET_EXCEEDED",
                path="budget.allowed_tools",
                hint="Add the tool to budget.allowed_tools.",
            )
        tools = ToolsConfig(self.spec.tools)
        name = tools.runner_name(cmd.program)
        runner = self._runners.get(name)
        if runner is None:
            runner = self._runners[name] = make_runner(
                name,
                tools,
                max_memory_gb=b.max_memory_gb,
                grace_s=self.spec.grace_s,
                poll_s=self.spec.poll_s,
            )
        self.log("info", f"run {cmd.program}", args=" ".join(cmd.args)[:200], runner=name)
        try:
            res: ExternalResult = runner.run(cmd, self.progress, self._cancel)
        except BudgetBreach as exc:
            request_cancel(self.spec.job_dir, exc.reason)
            raise Cancelled(exc.reason) from exc
        if res.returncode != 0:
            raise ExternalToolFailed(
                f"{cmd.program} exited with code {res.returncode}",
                returncode=res.returncode,
                log_tail=res.stderr_tail or res.stdout_tail,
            )
        return res

    # -- helpers for the job service -----------------------------------------------------

    def put_text(self, text: str, *, name: str, media_type: str) -> ArtifactRef:
        return self._cas.put_bytes(text.encode("utf-8"), name=name, media_type=media_type)


def execute_task(
    spec: ExecSpec, task_json: str, registry: Registry | None = None
) -> dict[str, Any]:
    """Run one task; returns a dict with "output", "cancelled" or "error"."""
    scratch: Path | None = None
    ok = False
    try:
        task = Task.model_validate_json(task_json)
        reg = registry or _registry(spec)
        analysis = reg.analysis(task.analysis)()
        scratch = (
            spec.job_dir
            / "scratch"
            / ("".join(c if c.isalnum() or c in "-_." else "_" for c in task.id))
        )
        if scratch.exists():
            shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True, exist_ok=True)
        ctx = JobRunContext(spec, task, scratch, registry=registry)
        if not spec.inline:
            ctx.store.update_task(spec.job_id, task.id, pid=os.getpid())
        payload = analysis.TaskPayload.model_validate(task.payload)
        ctx.check_cancelled()
        out = analysis.run_task(ctx, payload)
        if not isinstance(out, TaskOutput) or out.task_id != task.id:
            raise FrameworkError(
                f"run_task of {task.id!r} must return a TaskOutput with the same task_id",
                code="ANALYSIS_CONTRACT",
            )
        ok = True
        return {"output": out.model_dump_json()}
    except Cancelled as exc:
        return {"cancelled": cancel_reason(spec.job_dir) or str(exc) or "user"}
    except BaseException as exc:
        return {"error": to_error_info(exc).model_dump(mode="json")}
    finally:
        if scratch is not None and ok and not spec.keep_scratch:
            shutil.rmtree(scratch, ignore_errors=True)
