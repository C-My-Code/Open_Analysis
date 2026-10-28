# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Budget, estimate, request/status extensions and job errors (ai-integration.md section 10.4).

``Budget``, ``PlanEstimate``, ``AnalysisRequest.budget`` and ``JobStatus.cancel_reason`` are
contract additions A9. Until ``oa_framework.jobs`` carries them (see
``docs/change-requests/WP-107-budget-types.md``) they live here as *subclasses* of the frozen
framework types, so the base protocol stays satisfied.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from oa_framework.errors import ErrorInfo, FrameworkError
from oa_framework.jobs import AnalysisRequest, JobStatus
from oa_model._base import OAModel
from oa_model.errors import OAError

__all__ = [
    "Budget",
    "BudgetBreach",
    "BudgetExceeded",
    "JobNotFinished",
    "JobNotFound",
    "JobRequest",
    "JobStatusEx",
    "JobTimeout",
    "PlanEstimate",
    "clamp_budget",
]


class Budget(OAModel):
    max_runtime_s: float | None = Field(
        default=None, gt=0, description="Wall-clock limit for the whole job [s]."
    )
    max_memory_gb: float | None = Field(
        default=None,
        gt=0,
        description="Peak resident memory across workers and external processes [GB].",
    )
    max_cpus: int | None = Field(
        default=None, ge=1, description="Maximum concurrent worker processes/threads [1]."
    )
    allow_external: bool = Field(
        default=True, description="Whether external solver binaries may run."
    )
    allowed_tools: tuple[str, ...] | None = Field(
        default=None, description="Allow-list of logical tool names; None = all configured tools."
    )


def clamp_budget(requested: Budget | None, ceiling: Budget | None) -> Budget:
    """Effective budget: ``requested`` clamped field by field to ``ceiling``."""
    req = requested or Budget()
    if ceiling is None:
        return req

    def lo(a: float | None, b: float | None) -> float | None:
        return b if a is None else a if b is None else min(a, b)

    tools = req.allowed_tools
    if ceiling.allowed_tools is not None:
        tools = (
            ceiling.allowed_tools
            if tools is None
            else tuple(t for t in tools if t in ceiling.allowed_tools)
        )
    cpus = lo(req.max_cpus, ceiling.max_cpus)
    return Budget(
        max_runtime_s=lo(req.max_runtime_s, ceiling.max_runtime_s),
        max_memory_gb=lo(req.max_memory_gb, ceiling.max_memory_gb),
        max_cpus=None if cpus is None else int(cpus),
        allow_external=req.allow_external and ceiling.allow_external,
        allowed_tools=tools,
    )


class PlanEstimate(OAModel):
    tasks: int
    est_seconds: float  # sum of the task estimates
    est_wall_seconds: float = 0.0  # max(longest task, sum / parallelism)
    est_peak_memory_gb: float
    cpus: int
    needs: tuple[str, ...] = ()
    external: bool = False
    within_budget: bool = True
    budget: Budget = Budget()
    reasons: tuple[str, ...] = Field(default=(), description="Why the budget is exceeded.")
    warnings: tuple[str, ...] = ()


class JobRequest(AnalysisRequest):
    """``AnalysisRequest`` plus the budget and the calling surface."""

    budget: Budget | None = None
    caller: Literal["cli", "rest", "mcp", "assistant", "ui", "test"] = "test"


class JobStatusEx(JobStatus):
    """``JobStatus`` plus ``cancel_reason`` and the live budget usage."""

    cancel_reason: str | None = None  # "user" | "budget:max_runtime" | "budget:max_memory"
    budget_used: dict[str, float] = {}  # {"elapsed_s": ..., "peak_memory_gb": ...}


class BudgetExceeded(FrameworkError):
    """The plan estimate exceeds the effective budget; no task was started."""

    code = "BUDGET_EXCEEDED"

    def __init__(self, message: str, estimate: PlanEstimate) -> None:
        super().__init__(
            message,
            path="request.budget",
            hint="Raise the budget (up to the ceiling) or shrink the problem, see details.",
        )
        self.estimate = estimate
        self.details: dict[str, JsonValue] = {"estimate": estimate.model_dump(mode="json")}


class BudgetBreach(OAError):
    """Raised by runners when a running process violates the budget (kills it first)."""

    code = "BUDGET_EXCEEDED"

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason  # "budget:max_memory" | "budget:max_runtime"


class JobTimeout(FrameworkError):
    code = "TIMEOUT"


class JobNotFound(FrameworkError):
    code = "JOB_NOT_FOUND"


class JobNotFinished(FrameworkError):
    code = "JOB_NOT_FINISHED"

    def __init__(self, message: str, error: ErrorInfo | None = None) -> None:
        super().__init__(message, hint="Wait for the job to succeed or inspect its error.")
        self.error = error
