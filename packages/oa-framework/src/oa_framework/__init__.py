# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin protocols, registry, services, job and external-run types (interfaces.md section 3-5)."""

from oa_framework.analysis import (
    Analysis,
    ArtifactStore,
    Plan,
    PlanContext,
    Resources,
    RunContext,
    Task,
    TaskOutput,
)
from oa_framework.em import AdapterCapabilities, EMSolveOutput, PreparedRun, SolverAdapter
from oa_framework.errors import (
    DuplicateRegistration,
    ErrorInfo,
    FrameworkError,
    NoReader,
    PluginLoadError,
    RegistryError,
    UnknownPlugin,
    to_error_info,
)
from oa_framework.external import (
    CancelToken,
    ExternalCommand,
    ExternalResult,
    ExternalRunner,
    ToolInfo,
)
from oa_framework.ingest import Reader, ReadOptions, load
from oa_framework.jobs import (
    AnalysisRequest,
    JobEvent,
    JobService,
    JobState,
    JobStatus,
)
from oa_framework.registry import Registry
from oa_framework.rules import Rule, RuleContext
from oa_framework.services import DictServices, ImpedanceService, Services

__all__ = [
    "AdapterCapabilities",
    "Analysis",
    "AnalysisRequest",
    "ArtifactStore",
    "CancelToken",
    "DictServices",
    "DuplicateRegistration",
    "EMSolveOutput",
    "ErrorInfo",
    "ExternalCommand",
    "ExternalResult",
    "ExternalRunner",
    "FrameworkError",
    "ImpedanceService",
    "JobEvent",
    "JobService",
    "JobState",
    "JobStatus",
    "NoReader",
    "Plan",
    "PlanContext",
    "PluginLoadError",
    "PreparedRun",
    "ReadOptions",
    "Reader",
    "Registry",
    "RegistryError",
    "Resources",
    "Rule",
    "RuleContext",
    "RunContext",
    "Services",
    "SolverAdapter",
    "Task",
    "TaskOutput",
    "ToolInfo",
    "UnknownPlugin",
    "load",
    "to_error_info",
]
