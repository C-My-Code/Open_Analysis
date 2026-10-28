# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Job service protocol and types (interfaces.md section 4.4). Implemented by ``oa_jobs``."""

from __future__ import annotations

import builtins
from collections.abc import Iterator
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import JsonValue

from oa_framework.errors import ErrorInfo
from oa_model._base import OAModel
from oa_model.setup import AnalysisSetup
from oa_results.bundle import ResultBundle

JobState = Literal["queued", "planning", "running", "reducing", "succeeded", "failed", "cancelled"]


class AnalysisRequest(OAModel):
    project: Path  # oa-project.yaml
    setup: str  # setup name (file stem under oa/setups/) or inline:
    inline_setup: AnalysisSetup | None = None
    executor: Literal["inline", "process"] = "process"
    max_workers: int | None = None
    keep_scratch: bool = False
    priority: int = 0


class JobStatus(OAModel):
    id: str  # ULID
    state: JobState
    analysis: str
    setup: str
    progress: float  # 0..1 (weighted by task est_seconds)
    tasks_total: int
    tasks_done: int
    tasks_cached: int
    tasks_failed: int
    message: str = ""
    result_id: str | None = None
    error: ErrorInfo | None = None
    created_utc: str
    updated_utc: str


class JobEvent(OAModel):
    seq: int  # monotonically increasing per job
    job_id: str
    type: Literal[
        "state",
        "progress",
        "log",
        "task_started",
        "task_finished",
        "task_cached",
        "diagnostic",
        "result",
    ]
    task_id: str | None = None
    data: dict[str, JsonValue]
    utc: str


@runtime_checkable
class JobService(Protocol):
    def submit(self, req: AnalysisRequest) -> str: ...

    def status(self, job_id: str) -> JobStatus: ...

    def list(
        self, *, state: JobState | None = None, limit: int = 100
    ) -> builtins.list[JobStatus]: ...

    def cancel(self, job_id: str) -> None: ...

    def events(
        self, job_id: str, after_seq: int = 0, *, follow: bool = False
    ) -> Iterator[JobEvent]: ...

    def result(self, job_id: str) -> ResultBundle: ...

    def wait(self, job_id: str, timeout: float | None = None) -> JobStatus: ...
