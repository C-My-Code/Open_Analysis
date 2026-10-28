# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""2D quasi-static boundary element solver."""

from .geometry import MeshParams
from .solver import BemGeometry, BemSolution, prepare, solve_bem

__all__ = ["BemGeometry", "BemSolution", "MeshParams", "prepare", "solve_bem"]
