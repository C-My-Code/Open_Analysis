# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Analysis protocol and plan/task types (interfaces.md section 3.3)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar, Protocol, runtime_checkable

from pydantic import JsonValue

from oa_framework._types import BoardGeometry
from oa_framework.external import ExternalCommand, ExternalResult
from oa_framework.services import Services
from oa_model._base import OAModel
from oa_model.board import Board
from oa_model.common import Diagnostic
from oa_model.setup import ResolvedIntent
from oa_results.artifact import ArtifactRef
from oa_results.bundle import ResultBundle


class Resources(OAModel):
    cpus: int = 1
    memory_gb: float = 1.0
    gpu: bool = False
    needs: tuple[str, ...] = ()  # external capabilities, e.g. ("openems>=0.37",)
    est_seconds: float | None = None


class Task(OAModel):
    """A unit of cacheable work. MUST be JSON-serializable: executors ship it to other processes."""

    id: str  # unique in the plan, e.g. "xs:<key>", "port:P1"
    analysis: str  # Analysis.kind
    payload: dict[str, JsonValue]  # analysis-defined; validated by Analysis.TaskPayload
    inputs: tuple[ArtifactRef, ...] = ()  # files/blobs this task reads (content-addressed)
    depends_on: tuple[str, ...] = ()  # other task ids in the same plan
    resources: Resources = Resources()
    cache: bool = True


class Plan(OAModel):
    tasks: tuple[Task, ...]  # a DAG; topologically sortable
    reduce_inputs: tuple[str, ...] | None = None  # task ids reduce() needs; None = all


class TaskOutput(OAModel):
    task_id: str
    data: dict[str, JsonValue] = {}  # small JSON results
    artifacts: tuple[ArtifactRef, ...] = ()  # large outputs written via ctx.artifacts
    diagnostics: tuple[Diagnostic, ...] = ()


class ArtifactStore(Protocol):
    """Content-addressed store (the job CAS). The concrete store lives in ``oa_jobs``."""

    def put_bytes(self, data: bytes, *, name: str, media_type: str) -> ArtifactRef: ...

    def put_file(self, path: Path, *, name: str, media_type: str) -> ArtifactRef: ...

    def path(self, ref: ArtifactRef) -> Path: ...


class PlanContext(Protocol):
    board: Board  # physics already applied
    intent: ResolvedIntent
    setup_hash: str

    def geometry(self) -> BoardGeometry: ...  # lazily built and cached

    def services(self) -> Services: ...


class RunContext(Protocol):
    task: Task
    scratch: Path  # empty per-task working directory
    threads: int

    def board(self) -> Board: ...  # loaded lazily from the job's board artifact

    def geometry(self) -> BoardGeometry: ...

    def services(self) -> Services: ...

    def progress(self, fraction: float, message: str = "") -> None: ...  # 0..1, rate-limited

    def check_cancelled(self) -> None:
        """Raises ``Cancelled``."""
        ...

    def log(self, level: str, message: str, **fields: object) -> None: ...

    def artifacts(self) -> ArtifactStore: ...

    def run_external(self, cmd: ExternalCommand) -> ExternalResult: ...


@runtime_checkable
class Analysis(Protocol):
    kind: ClassVar[str]  # dotted: "impedance.xsection", "erc", "pi.dc", "em.sparams", ...
    version: ClassVar[str]  # bump when results change; part of the cache key
    title: ClassVar[str]
    Config: ClassVar[type[OAModel]]  # setup schema -> UI form via JSON Schema
    TaskPayload: ClassVar[type[OAModel]]

    def validate_setup(self, ctx: PlanContext, cfg: OAModel) -> tuple[Diagnostic, ...]: ...

    def plan(self, ctx: PlanContext, cfg: OAModel) -> Plan: ...

    def run_task(self, ctx: RunContext, payload: OAModel) -> TaskOutput: ...

    def reduce(
        self, ctx: RunContext, cfg: OAModel, outputs: Mapping[str, TaskOutput]
    ) -> ResultBundle: ...
