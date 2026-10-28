# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The M1 rule catalogue (interfaces.md section 3.6)."""

from __future__ import annotations

from oa_erc.rules.crosstalk import ParallelCoupling
from oa_erc.rules.diff_pair import GapVariation, IntraPairSkew, UncoupledLength
from oa_erc.rules.edge import NearBoardEdge, NearPlaneEdge, StitchSpacing
from oa_erc.rules.emi import PlaneCavity
from oa_erc.rules.impedance import (
    ImpedanceOutOfTolerance,
    LongStub,
    Neckdown,
    WidthMismatch,
)
from oa_erc.rules.power import DecapDistance
from oa_erc.rules.return_path import (
    ReferenceChange,
    SplitCrossing,
    TransitionWithoutReturn,
    Unreferenced,
    VoidCluster,
)
from oa_erc.rules.rf import FencePitch
from oa_erc.rules.via import ViaCount, ViaStub

BUILTIN_RULES = (
    SplitCrossing,
    VoidCluster,
    ReferenceChange,
    TransitionWithoutReturn,
    Unreferenced,
    NearPlaneEdge,
    NearBoardEdge,
    StitchSpacing,
    WidthMismatch,
    ImpedanceOutOfTolerance,
    Neckdown,
    LongStub,
    UncoupledLength,
    IntraPairSkew,
    GapVariation,
    ViaStub,
    ViaCount,
    ParallelCoupling,
    DecapDistance,
    FencePitch,
    PlaneCavity,
)

__all__ = ["BUILTIN_RULES"]
