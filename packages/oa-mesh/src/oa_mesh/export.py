# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Debug export of meshes to meshio / VTU (open the result in ParaView)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from oa_mesh.types import SheetMesh, XsMesh

__all__ = ["to_meshio", "write_vtu"]


def to_meshio(mesh: XsMesh | SheetMesh) -> Any:
    """Convert to a ``meshio.Mesh`` (z = 0 for cross-sections, ``mesh.z`` for sheets).

    Cross-sections carry ``region`` as cell data, a ``conductor`` node tag (index into
    ``CrossSection.conductors`` + 1, 0 for none) and ``outer`` (1 on the box). Sheets carry a
    ``contact`` node tag (index into ``mesh.contacts`` + 1, 0 for none).
    """
    import meshio  # type: ignore[import-untyped]

    n = mesh.nodes.array
    z = 0.0
    if isinstance(mesh, SheetMesh) and mesh.z is not None:
        z = mesh.z
    pts = np.column_stack([n, np.full(len(n), z)])
    tri = mesh.triangles.array
    if isinstance(mesh, XsMesh):
        kind = "triangle" if mesh.order == 1 else "triangle6"
        # meshio's VTK-style triangle6 ordering is v0 v1 v2 m01 m12 m20, same as gmsh.
        cond = np.zeros(len(n), dtype=np.int64)
        for i, es in enumerate(mesh.conductor_edges):
            cond[np.unique(es.edges.array)] = i + 1
        outer = np.zeros(len(n), dtype=np.int64)
        outer[np.unique(mesh.outer_edges.edges.array)] = 1
        return meshio.Mesh(
            pts,
            [(kind, tri)],
            point_data={"conductor": cond, "outer": outer},
            cell_data={"region": [mesh.region.array]},
        )
    contact = np.zeros(len(n), dtype=np.int64)
    for i, c in enumerate(mesh.contacts):
        contact[c.nodes.array] = i + 1
    return meshio.Mesh(pts, [("triangle", tri)], point_data={"contact": contact})


def write_vtu(mesh: XsMesh | SheetMesh, path: str | Path) -> Path:
    """Write ``mesh`` as VTU (XML, ASCII-free binary) for debugging; returns the path."""
    p = Path(path)
    to_meshio(mesh).write(p, file_format="vtu")
    return p
