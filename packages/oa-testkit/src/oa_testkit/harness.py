# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""In-process analysis harness: ``run_inline(analysis, board, setup, registry)``.

Runs an ``Analysis`` (interfaces.md section 3.3) the way ``oa_jobs``' inline executor does, but
without a job database, cache, worker processes or files: ``validate_setup`` -> ``plan`` ->
``run_task`` for every task in dependency order -> ``reduce``, with an in-memory ``RunContext``.
Wave-1 analyses test against this instead of ``oa_jobs``.

Contract checks done on the way (they are cheap and catch real bugs in analyses):

- ``Task`` and ``TaskOutput`` survive a JSON round trip (executors ship them between processes).
- Task ids are unique, ``depends_on``/``reduce_inputs`` refer to existing tasks, the DAG is acyclic.
- Every task belongs to the analysis (``task.analysis == analysis.kind``) and every output carries
  the id of its task.

Not done: caching, process isolation, concurrency (tasks run sequentially in topological order).
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any

from oa_framework.analysis import Analysis, Plan, Task, TaskOutput
from oa_framework.registry import Registry
from oa_framework.services import DictServices, Services
from oa_model.board import Board
from oa_model.common import Diagnostic
from oa_model.errors import Cancelled, SetupError
from oa_model.physics import PhysicsOverlay, apply_physics
from oa_model.setup import AnalysisSetup, resolve_intent
from oa_model.setup.intent import DesignIntent
from oa_results.artifact import ArtifactRef
from oa_results.bundle import ResultBundle

__all__ = [
    "InMemoryArtifactStore",
    "InlineRun",
    "run_inline",
    "run_inline_full",
]

REDUCE_TASK_ID = "__reduce__"


class InMemoryArtifactStore:
    """Content-addressed blob store held in a dict (stand-in for ``ctx.artifacts()``).

    Implements ``oa_framework.analysis.ArtifactStore`` (``put_bytes``, ``put_file``, ``path``)
    plus ``get``/``has`` for assertions. ``path`` materializes the blob in a private temp
    directory that is removed by :meth:`close`.
    """

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}
        self._tmp: Path | None = None

    def put_bytes(self, data: bytes, *, name: str, media_type: str) -> ArtifactRef:
        digest = hashlib.sha256(data).hexdigest()
        self._blobs[digest] = bytes(data)
        return ArtifactRef(sha256=digest, media_type=media_type, name=name, size=len(data))

    def put_file(self, path: Path, *, name: str, media_type: str) -> ArtifactRef:
        return self.put_bytes(Path(path).read_bytes(), name=name, media_type=media_type)

    def get(self, ref: ArtifactRef) -> bytes:
        return self._blobs[ref.sha256]

    def has(self, ref: ArtifactRef) -> bool:
        return ref.sha256 in self._blobs

    def path(self, ref: ArtifactRef) -> Path:
        if self._tmp is None:
            self._tmp = Path(tempfile.mkdtemp(prefix="oa_inline_cas_"))
        target = self._tmp / ref.sha256
        if not target.exists():
            target.write_bytes(self._blobs[ref.sha256])
        return target

    def close(self) -> None:
        if self._tmp is not None:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None

    def __len__(self) -> int:
        return len(self._blobs)


def _services_from(registry: Registry | None, services: Any) -> Services:
    if services is None:
        return registry.services() if registry is not None else DictServices()
    return DictServices(services) if isinstance(services, Mapping) else services  # type: ignore[no-any-return]


class _Shared:
    """State shared by the plan and run contexts of one inline run."""

    def __init__(
        self,
        board: Board,
        intent: Any,
        setup_hash: str,
        services: Any,
        geometry: Callable[[Board], Any] | None,
        artifacts: InMemoryArtifactStore,
        cancel: threading.Event | None,
        threads: int,
    ) -> None:
        self.board = board
        self.intent = intent
        self.setup_hash = setup_hash
        self.services = services
        self._geometry_factory = geometry
        self._geometry: Any = None
        self.artifacts = artifacts
        self.cancel = cancel
        self.threads = threads
        self.progress: list[tuple[str, float, str]] = []
        self.logs: list[tuple[str, str, str, dict[str, Any]]] = []

    def geometry(self) -> Any:
        if self._geometry is None:
            if self._geometry_factory is None:
                raise NotImplementedError(
                    "this inline run has no geometry service; pass geometry=<callable(Board)> "
                    "to run_inline (oa_geometry is not a dependency of oa_testkit)"
                )
            self._geometry = self._geometry_factory(self.board)
        return self._geometry


class InlinePlanContext:
    """``PlanContext`` for ``validate_setup`` and ``plan``."""

    def __init__(self, shared: _Shared) -> None:
        self._s = shared
        self.board = shared.board
        self.intent = shared.intent
        self.setup_hash = shared.setup_hash

    def geometry(self) -> Any:
        return self._s.geometry()

    def services(self) -> Any:
        return self._s.services


class InlineRunContext:
    """In-memory ``RunContext`` (interfaces.md section 3.3)."""

    def __init__(self, shared: _Shared, task: Task, scratch: Path) -> None:
        self._s = shared
        self.task = task
        self.scratch = scratch
        self.threads = shared.threads

    def board(self) -> Board:
        return self._s.board

    def geometry(self) -> Any:
        return self._s.geometry()

    def services(self) -> Any:
        return self._s.services

    def progress(self, fraction: float, message: str = "") -> None:
        if not 0.0 <= fraction <= 1.0:
            raise ValueError(f"progress fraction must be in [0, 1], got {fraction}")
        self._s.progress.append((self.task.id, fraction, message))

    def check_cancelled(self) -> None:
        if self._s.cancel is not None and self._s.cancel.is_set():
            raise Cancelled("cancelled")

    def log(self, level: str, message: str, **fields: Any) -> None:
        self._s.logs.append((self.task.id, level, message, fields))

    def artifacts(self) -> InMemoryArtifactStore:
        return self._s.artifacts

    def run_external(self, cmd: Any) -> Any:
        raise NotImplementedError(
            "run_inline does not execute external tools; tests that need them are marked "
            "@pytest.mark.external and use the real job service"
        )


@dataclass
class InlineRun:
    """Everything a test may want to look at after ``run_inline_full``."""

    bundle: ResultBundle
    plan: Plan
    order: tuple[str, ...]  # task ids in execution order
    outputs: dict[str, TaskOutput]
    diagnostics: tuple[Diagnostic, ...]  # from validate_setup (warnings/info only; errors raise)
    artifacts: InMemoryArtifactStore
    progress: list[tuple[str, float, str]] = field(default_factory=list)
    logs: list[tuple[str, str, str, dict[str, Any]]] = field(default_factory=list)


def _check_plan(plan: Plan, kind: str) -> list[str]:
    ids = [t.id for t in plan.tasks]
    if len(set(ids)) != len(ids):
        dups = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"plan has duplicate task ids: {dups}")
    known = set(ids)
    graph: dict[str, set[str]] = {}
    for t in plan.tasks:
        if t.analysis != kind:
            raise ValueError(f"task {t.id!r} has analysis {t.analysis!r}, expected {kind!r}")
        missing = [d for d in t.depends_on if d not in known]
        if missing:
            raise ValueError(f"task {t.id!r} depends on unknown tasks {missing}")
        graph[t.id] = set(t.depends_on)
        if Task.model_validate_json(t.model_dump_json()) != t:
            raise ValueError(f"task {t.id!r} does not survive a JSON round trip")
    if plan.reduce_inputs is not None:
        missing = [i for i in plan.reduce_inputs if i not in known]
        if missing:
            raise ValueError(f"reduce_inputs refer to unknown tasks {missing}")
    try:
        return list(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        raise ValueError(f"plan has a dependency cycle: {exc.args[1]}") from exc


def run_inline_full(
    analysis: Any,
    board: Board,
    setup: AnalysisSetup,
    registry: Registry | None = None,
    *,
    intent: DesignIntent | None = None,
    physics: PhysicsOverlay | None = None,
    services: Any = None,
    geometry: Callable[[Board], Any] | None = None,
    cancel: threading.Event | None = None,
    threads: int = 1,
    keep_scratch: bool = False,
) -> InlineRun:
    """Run ``analysis`` end to end in this process and return the bundle plus run details.

    ``analysis`` is an ``Analysis`` instance, a class (instantiated without arguments) or a kind
    string resolved through ``registry``. ``registry`` supplies services unless ``services`` (a
    ``Services`` object or a ``{name: service}`` mapping) is given. ``physics`` is applied to
    ``board`` first, as the job service does. ``geometry`` builds the ``BoardGeometry``
    returned by ``ctx.geometry()``.
    """
    if isinstance(analysis, str):
        if registry is None:
            raise ValueError("an analysis kind string needs a registry to resolve it")
        analysis = registry.analysis(analysis)
    inst: Analysis = analysis() if isinstance(analysis, type) else analysis
    if setup.analysis != inst.kind:
        raise ValueError(f"setup is for analysis {setup.analysis!r}, not {inst.kind!r}")

    work = apply_physics(board, physics) if physics is not None else board
    shared = _Shared(
        board=work,
        intent=resolve_intent(work, intent if intent is not None else DesignIntent()),
        setup_hash="sha256:" + hashlib.sha256(setup.model_dump_json().encode()).hexdigest(),
        services=_services_from(registry, services),
        geometry=geometry,
        artifacts=InMemoryArtifactStore(),
        cancel=cancel,
        threads=threads,
    )
    cfg = inst.Config.model_validate(setup.config)

    pctx = InlinePlanContext(shared)
    diags = tuple(inst.validate_setup(pctx, cfg))
    errors = [d for d in diags if d.severity == "error"]
    if errors:
        err = SetupError("; ".join(f"{d.code}: {d.message}" for d in errors))
        err.diagnostics = diags  # type: ignore[attr-defined]
        raise err

    plan = inst.plan(pctx, cfg)
    order = _check_plan(plan, inst.kind)
    by_id = {t.id: t for t in plan.tasks}

    scratch_root = Path(tempfile.mkdtemp(prefix="oa_inline_"))
    try:
        outputs: dict[str, TaskOutput] = {}
        for tid in order:
            task = by_id[tid]
            scratch = scratch_root / tid.replace(":", "_").replace("/", "_")
            scratch.mkdir(parents=True, exist_ok=True)
            ctx = InlineRunContext(shared, task, scratch)
            ctx.check_cancelled()
            payload = inst.TaskPayload.model_validate(task.payload)
            out = inst.run_task(ctx, payload)
            if out.task_id != task.id:
                raise ValueError(f"run_task for {task.id!r} returned output for {out.task_id!r}")
            if TaskOutput.model_validate_json(out.model_dump_json()) != out:
                raise ValueError(f"output of task {task.id!r} does not survive a JSON round trip")
            outputs[tid] = out

        wanted = plan.reduce_inputs if plan.reduce_inputs is not None else tuple(order)
        reduce_in = {tid: outputs[tid] for tid in order if tid in set(wanted)}
        rtask = Task(id=REDUCE_TASK_ID, analysis=inst.kind, payload={}, cache=False)
        rscratch = scratch_root / "reduce"
        rscratch.mkdir(parents=True, exist_ok=True)
        rctx = InlineRunContext(shared, rtask, rscratch)
        rctx.check_cancelled()
        bundle = inst.reduce(rctx, cfg, reduce_in)
    finally:
        if not keep_scratch:
            shutil.rmtree(scratch_root, ignore_errors=True)
        shared.artifacts.close()

    return InlineRun(
        bundle=bundle,
        plan=plan,
        order=tuple(order),
        outputs=outputs,
        diagnostics=diags,
        artifacts=shared.artifacts,
        progress=shared.progress,
        logs=shared.logs,
    )


def run_inline(
    analysis: Any,
    board: Board,
    setup: AnalysisSetup,
    registry: Registry | None = None,
    **kwargs: Any,
) -> ResultBundle:
    """validate_setup -> plan -> run_task x N -> reduce; returns the ``ResultBundle``.

    See :func:`run_inline_full` for the keyword options and for access to per-task outputs.
    """
    return run_inline_full(analysis, board, setup, registry, **kwargs).bundle
