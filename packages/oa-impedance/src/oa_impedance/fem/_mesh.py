# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""skfem mesh + boundary bookkeeping built from an ``oa_mesh.XsMesh`` (P1 geometry, P2 fields)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt
from skfem import Basis, ElementTriP2, MeshTri  # type: ignore[import-untyped,unused-ignore]

from oa_mesh import XsMesh

I64 = npt.NDArray[np.int64]

__all__ = ["FemMesh", "build"]


@dataclass
class FemMesh:
    """Mesh, P2 basis and the facet sets of every conductor and of the outer box."""

    mesh: MeshTri
    region: I64  # per element: >= 0 dielectric, -1 air, <= -2 conductor interior
    cond_ids: tuple[str, ...]
    cond_facets: list[I64]
    outer_facets: I64
    box: tuple[float, float, float, float]
    basis: Basis = field(init=False)

    def __post_init__(self) -> None:
        self.basis = Basis(self.mesh, ElementTriP2())

    def dofs_of(self, facets: I64) -> I64:
        if len(facets) == 0:
            return np.zeros(0, dtype=np.int64)
        return np.unique(self.basis.get_dofs(facets).all())


def build(m: XsMesh) -> FemMesh:
    nodes = m.nodes.array
    tri = m.triangles.array[:, :3]
    mesh = MeshTri(np.ascontiguousarray(nodes.T), np.ascontiguousarray(tri.T))
    fs = np.sort(mesh.facets.T, axis=1)
    look = {(int(a), int(b)): i for i, (a, b) in enumerate(fs)}

    def facets(edges: I64) -> I64:
        e = np.sort(edges[:, :2], axis=1)
        return np.array([look[(int(a), int(b))] for a, b in e], dtype=np.int64)

    return FemMesh(
        mesh=mesh,
        region=m.region.array.astype(np.int64),
        cond_ids=tuple(str(e.source_id) for e in m.conductor_edges),
        cond_facets=[facets(e.edges.array) for e in m.conductor_edges],
        outer_facets=facets(m.outer_edges.edges.array),
        box=m.box,
    )
