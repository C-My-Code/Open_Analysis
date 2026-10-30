# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Graded rectilinear mesher shared by the FDTD solvers (ADR-0010 section 2, WP-505).

Pure NumPy. The seed/smoothing rules are those of ``oa_em_openems.mesh``; :func:`build_axis` adds
the layer, copper-face and feature rules on top.
"""

from oa_mesh.rect._axis import (
    MERGE_TOL,
    AxisMesh,
    estimate_timestep,
    merge_seeds,
    round_lines,
    smooth_axis,
    third_rule_lines,
)
from oa_mesh.rect._build import AxisSpec, CopperFace, build_axis, max_cell_size

__all__ = [
    "MERGE_TOL",
    "AxisMesh",
    "AxisSpec",
    "CopperFace",
    "build_axis",
    "estimate_timestep",
    "max_cell_size",
    "merge_seeds",
    "round_lines",
    "smooth_axis",
    "third_rule_lines",
]
