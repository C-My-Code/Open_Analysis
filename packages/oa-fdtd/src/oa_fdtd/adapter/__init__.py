# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""oa-fdtd solver adapter (WP-507): ``EMProblem`` to a batched GPU run and back."""

from oa_fdtd.adapter.adapter import FdtdAdapter, OaFdtdAdapter, default_assessor
from oa_fdtd.adapter.sim import SolveOptions, SolveResult, options_from_problem, solve_problem

__all__ = [
    "FdtdAdapter",
    "OaFdtdAdapter",
    "SolveOptions",
    "SolveResult",
    "default_assessor",
    "options_from_problem",
    "solve_problem",
]
