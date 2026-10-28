# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""VTU export of ``FieldResult`` pieces (via meshio)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from oa_results.fields import FieldPiece, FieldResult, RasterGrid2D, TetMesh3D, TriMesh2D

__all__ = ["to_vtu"]


def _grid(g: RasterGrid2D) -> tuple[Any, str, Any]:
    xs = g.origin[0] + g.dx * np.arange(g.nx + 1)
    ys = g.origin[1] + g.dy * np.arange(g.ny + 1)
    pts = np.array([(x, y, 0.0) for y in ys for x in xs])
    w = g.nx + 1
    quads = [
        (j * w + i, j * w + i + 1, (j + 1) * w + i + 1, (j + 1) * w + i)
        for j in range(g.ny)
        for i in range(g.nx)
    ]
    return pts, "quad", np.array(quads, dtype=np.int64)


def _piece_mesh(piece: FieldPiece) -> tuple[Any, str, Any]:
    m = piece.mesh
    if isinstance(m, TriMesh2D):
        xy = np.asarray(m.nodes.array)
        pts = np.column_stack([xy, np.full(len(xy), m.z or 0.0)])
        return pts, "triangle", np.asarray(m.triangles.array, dtype=np.int64)
    if isinstance(m, TetMesh3D):
        return np.asarray(m.nodes.array), "tetra", np.asarray(m.tets.array, dtype=np.int64)
    return _grid(m)


def to_vtu(field: FieldResult, path: str | Path) -> list[Path]:
    """Write one ``.vtu`` per piece (``path`` for a single piece, else ``<stem>_<i>.vtu``).

    Node fields become point data, cell fields cell data. Returns the written paths.
    """
    import meshio  # type: ignore[import-untyped]

    base = Path(path)
    written: list[Path] = []
    for i, piece in enumerate(field.pieces):
        pts, cell_type, cells = _piece_mesh(piece)
        point_data: dict[str, Any] = {}
        cell_data: dict[str, Any] = {}
        for fd in piece.fields:
            vals = np.asarray(fd.values.array)
            if fd.location == "node":
                point_data[fd.name] = vals
            else:
                cell_data[fd.name] = [vals]
        out = base if len(field.pieces) == 1 else base.with_name(f"{base.stem}_{i}{base.suffix}")
        meshio.write(
            str(out),
            meshio.Mesh(pts, [(cell_type, cells)], point_data=point_data, cell_data=cell_data),
            file_format="vtu",
        )
        written.append(out)
    return written
