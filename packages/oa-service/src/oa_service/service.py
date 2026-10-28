# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``OAService`` protocol every transport programs against (ai-integration.md section 2.3).

Two implementations ship: :class:`oa_service.local.LocalService` (real ingest, jobs and results)
and :class:`oa_service.testing.FakeService` (testkit boards and canned results). Command handlers
in :mod:`oa_service.catalog` use only this protocol, so both behave identically to the adapters.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from oa_framework.ingest import ReadOptions
from oa_model.board import Board
from oa_model.setup import AnalysisSetup
from oa_results.bundle import ResultBundle
from oa_service.commands import CallContext
from oa_service.models import (
    AnalysisInfo,
    BoardSummary,
    ExportIn,
    ExportInfo,
    JobBrief,
    JobEventView,
    JobHandle,
    JobKind,
    JobStatusOut,
    NetDetail,
    NetInfo,
    NetQuery,
    ProjectHandle,
    RuleInfo,
    RunAnalysisIn,
    RunDryRun,
    SetupPutOut,
    SetupSummary,
    SetupValidation,
    StackupView,
    XsImpedance,
    XsImpedanceIn,
)
from oa_service.types import Page

__all__ = ["OAService", "ProjectSession", "RunRequest"]

RunRequest = RunAnalysisIn
"""Alias used by the spec text: what to run, with which budget, and whether to wait."""


@runtime_checkable
class ProjectSession(Protocol):
    """One opened project: board views and stored setups."""

    id: str  # "p_" + sha1(abs project path)[:12]; stable across restarts
    board: Board  # physics applied

    def handle(self) -> ProjectHandle: ...

    def summary(self) -> BoardSummary: ...

    def nets(self, q: NetQuery) -> Page[NetInfo]: ...

    def net(self, name_or_id: str) -> NetDetail: ...

    def stackup(self, f: float | None = None) -> StackupView: ...

    def geometry(self) -> Any: ...  # oa_framework BoardGeometry (lazy, cached)

    def setups(self) -> tuple[SetupSummary, ...]: ...

    def setup(self, name: str) -> AnalysisSetup: ...

    def put_setup(self, setup: AnalysisSetup) -> SetupPutOut: ...

    def resolve_net(self, name_or_id: str) -> str: ...


@runtime_checkable
class OAService(Protocol):
    """Everything a command handler may call. Methods raise ``ServiceError`` or ``OAError``."""

    # projects
    def open(
        self,
        path: Path,
        *,
        read_options: ReadOptions | None = None,
        physics: Path | None = None,
        intent: Path | None = None,
    ) -> ProjectHandle: ...

    def projects(self) -> Sequence[ProjectHandle]: ...

    def project(self, project_id: str) -> ProjectSession: ...

    def reload(self, project_id: str) -> ProjectHandle: ...

    # stored setups
    def validate_setup(self, project_id: str, name: str) -> SetupValidation: ...

    def put_setup(self, project_id: str, setup: AnalysisSetup) -> SetupPutOut: ...

    # plugin discovery
    def analyses(self) -> tuple[AnalysisInfo, ...]: ...

    def rules(self) -> tuple[RuleInfo, ...]: ...

    # jobs
    def dry_run(self, project_id: str, req: RunRequest, ctx: CallContext) -> RunDryRun: ...

    def submit(self, project_id: str, req: RunRequest, ctx: CallContext) -> JobHandle: ...

    def job(
        self, job_id: str, *, wait_s: float = 0.0, events_after_seq: int | None = None
    ) -> JobStatusOut: ...

    def job_handle(self, job_id: str) -> JobHandle: ...

    def events(self, job_id: str, after_seq: int = 0, limit: int = 100) -> list[JobEventView]: ...

    def jobs(
        self, project_id: str, *, state: JobKind | None = None, limit: int = 100
    ) -> Sequence[JobBrief]: ...

    def cancel(self, job_id: str) -> JobBrief: ...

    # results
    def result(self, result_id: str) -> ResultBundle: ...

    def export(self, req: ExportIn, ctx: CallContext) -> ExportInfo: ...

    # impedance (through the Analysis/Service registry)
    def xs_impedance(self, req: XsImpedanceIn, ctx: CallContext) -> XsImpedance: ...
