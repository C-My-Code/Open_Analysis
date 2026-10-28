# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""gmsh 2D/3D meshing (WP-109: 2D cross-sections and copper sheets).

Importing this package, or any of its types, never imports or initializes gmsh; the meshers load
it lazily inside ``oa_mesh._session.gmsh_session()``.
"""

from oa_mesh.errors import MeshError
from oa_mesh.export import to_meshio, write_vtu
from oa_mesh.sheets import mesh_copper_sheets
from oa_mesh.types import (
    CleanOptions,
    CleanReport,
    Contact,
    ContactNodes,
    EdgeSet,
    MeshQuality,
    PhysicalGroup,
    SheetMesh,
    SheetPiece,
    SizeField,
    XsMesh,
    mesh_digest,
)
from oa_mesh.xsection import mesh_cross_section

__all__ = [
    "CleanOptions",
    "CleanReport",
    "Contact",
    "ContactNodes",
    "EdgeSet",
    "MeshError",
    "MeshQuality",
    "PhysicalGroup",
    "SheetMesh",
    "SheetPiece",
    "SizeField",
    "XsMesh",
    "mesh_copper_sheets",
    "mesh_cross_section",
    "mesh_digest",
    "to_meshio",
    "write_vtu",
]
