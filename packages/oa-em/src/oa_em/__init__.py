# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""EMProblem flow: region to S-parameters (``em.sparams``, WP-205)."""

from oa_em.analysis import EmSparams
from oa_em.config import BoundaryConfig, EmSparamsConfig, RegionConfig
from oa_em.problem import Built, PortInfo, build_problem, choose_boundaries

__all__ = [
    "BoundaryConfig",
    "Built",
    "EmSparams",
    "EmSparamsConfig",
    "PortInfo",
    "RegionConfig",
    "build_problem",
    "choose_boundaries",
]
