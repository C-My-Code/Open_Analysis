# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""3D OCC mesher for ``Structure3D`` (WP-206). Importing this package never loads gmsh."""

from oa_mesh.occ3d.glb import export_glb
from oa_mesh.occ3d.mesher import build_occ_model, mesh_problem, write_msh
from oa_mesh.occ3d.types import GroupInfo, MeshStats, OccModel, OccOptions, OccReport

__all__ = [
    "GroupInfo",
    "MeshStats",
    "OccModel",
    "OccOptions",
    "OccReport",
    "build_occ_model",
    "export_glb",
    "mesh_problem",
    "write_msh",
]
