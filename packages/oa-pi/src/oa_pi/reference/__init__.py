# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent reference solvers used to cross-check the FEM (not part of the analysis path)."""

from oa_pi.reference.raster import RasterSolution, solve_raster

__all__ = ["RasterSolution", "solve_raster"]
