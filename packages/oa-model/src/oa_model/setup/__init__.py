# SPDX-License-Identifier: GPL-3.0-or-later
"""Setup-side schemas: intent, ports, project manifest and analysis setup."""

from oa_model.setup.intent import (
    DesignIntent,
    NetIntentResolved,
    NetSelector,
    PowerIntent,
    ResolvedIntent,
    SignalIntent,
    load_intent,
    resolve_intent,
)
from oa_model.setup.ports import (
    CoaxPort,
    LumpedPort,
    NearestReferenceTerminal,
    PadTerminal,
    PinGroupTerminal,
    PlanePairPort,
    PointTerminal,
    Port,
    Terminal,
    TraceEndPort,
)
from oa_model.setup.project import (
    AnalysisSetup,
    ProjectManifest,
    ReadOptions,
    Waiver,
    load_project,
    load_setup,
)

__all__ = [
    "AnalysisSetup",
    "CoaxPort",
    "DesignIntent",
    "LumpedPort",
    "NearestReferenceTerminal",
    "NetIntentResolved",
    "NetSelector",
    "PadTerminal",
    "PinGroupTerminal",
    "PlanePairPort",
    "PointTerminal",
    "Port",
    "PowerIntent",
    "ProjectManifest",
    "ReadOptions",
    "ResolvedIntent",
    "SignalIntent",
    "Terminal",
    "TraceEndPort",
    "Waiver",
    "load_intent",
    "load_project",
    "load_setup",
    "resolve_intent",
]
