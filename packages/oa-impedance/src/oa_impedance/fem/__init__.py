# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""FEM reference solver (WP-202): importing this package registers ``method="fem"``."""

from oa_impedance import methods as _methods
from oa_impedance.fem.solver import (
    FEM_VERSION,
    FemParams,
    FemSolution,
    solve_fem,
    solve_fem_raw,
    solve_loss,
    with_fem_loss,
)

__all__ = [
    "FEM_VERSION",
    "FemParams",
    "FemSolution",
    "solve_fem",
    "solve_fem_raw",
    "solve_loss",
    "with_fem_loss",
]

_methods.register("fem", solve_fem, replace=True)
