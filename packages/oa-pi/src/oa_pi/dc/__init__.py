# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""DC IR drop: network extraction, FEM solve, results (WP-204)."""

from oa_pi.dc.analysis import DcTaskPayload, PiDcAnalysis
from oa_pi.dc.config import (
    DcConfig,
    LimitsConfig,
    LoadConfig,
    MeshConfig,
    RailConfig,
    SenseConfig,
    VrmConfig,
)
from oa_pi.dc.fem import DcSolution, solve_fem
from oa_pi.dc.network import DcNetwork, build_network

__all__ = [
    "DcConfig",
    "DcNetwork",
    "DcSolution",
    "DcTaskPayload",
    "LimitsConfig",
    "LoadConfig",
    "MeshConfig",
    "PiDcAnalysis",
    "RailConfig",
    "SenseConfig",
    "VrmConfig",
    "build_network",
    "solve_fem",
]
