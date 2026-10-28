# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared builders and mesh checks for the oa_mesh.occ3d tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import meshio
import numpy as np

from oa_mesh.occ3d import MeshStats, OccModel, write_msh
from oa_model.arrays import Points
from oa_model.em import Box3, ConductorBody, MaterialSlab, Structure3D, ViaBody
from oa_model.geometry import Polygon
from oa_model.materials import ConductorMaterial, DielectricMaterial, DjordjevicSarkar
from oa_testkit import builders as bld
from oa_testkit.emproblems import _materials


def rect_poly(x0: float, y0: float, x1: float, y1: float) -> Polygon:
    return Polygon(exterior=Points(bld.rect_ring(x0, y0, x1, y1)))


def fr4_materials() -> tuple[DielectricMaterial | ConductorMaterial, ...]:
    return _materials(bld.microstrip_board())


def thick_board(
    *,
    size: tuple[float, float] = (4e-3, 3e-3),
    cu: float = 18e-6,
    board: float = 1.6e-3,
    trace_w: float = 0.3e-3,
    air: float = 0.8e-3,
) -> Structure3D:
    """1.6 mm 4-layer board with 18 um copper, a trace on L1 over a plane on L2, 4 x 3 mm."""
    lx, ly = size
    # copper layers: L1 top, L2, L3, L4 bottom; dielectrics between
    h1, h3 = 0.2e-3, 0.2e-3
    core = board - 4 * cu - h1 - h3
    z = 0.0
    l4 = (z, z + cu)
    z += cu
    d3 = (z, z + h3)
    z += h3
    l3 = (z, z + cu)
    z += cu
    dc = (z, z + core)
    z += core
    l2 = (z, z + cu)
    z += cu
    d1 = (z, z + h1)
    z += h1
    l1 = (z, z + cu)
    zmax = z + cu + air
    box = Box3(xmin=0.0, ymin=-ly / 2, zmin=0.0, xmax=lx, ymax=ly / 2, zmax=zmax)

    def plane(cid: str, lz: tuple[float, float]) -> ConductorBody:
        return ConductorBody(
            id=cid,
            net="GND",
            role="reference",
            layer=cid[:2],
            z_bottom=lz[0],
            z_top=lz[1],
            polygons=(rect_poly(0.0, -ly / 2, lx, ly / 2),),
        )

    trace = ConductorBody(
        id="L1:SIG",
        net="SIG",
        role="signal",
        layer="L1",
        z_bottom=l1[0],
        z_top=l1[1],
        polygons=(rect_poly(0.0, -trace_w / 2, lx, trace_w / 2),),
    )
    return Structure3D(
        bbox=box,
        materials=fr4_materials(),
        slabs=tuple(
            MaterialSlab(material="FR4_generic", z_bottom=a, z_top=b) for a, b in (d3, dc, d1)
        ),
        conductors=(trace, plane("L2:GND", l2), plane("L3:GND", l3), plane("L4:GND", l4)),
        vias=(),
    )


def with_vias(st: Structure3D, vias: tuple[ViaBody, ...]) -> Structure3D:
    return st.model_copy(update={"vias": vias})


@dataclass
class MshData:
    points: np.ndarray
    tets: np.ndarray
    tet_tag: np.ndarray
    tris: np.ndarray
    tri_tag: np.ndarray
    names: dict[str, int]


def read_msh(path: Path) -> MshData:
    m = meshio.read(path)
    tets, ttag, tris, trtag = [], [], [], []
    phys = m.cell_data["gmsh:physical"]
    for blk, tags in zip(m.cells, phys, strict=True):
        if blk.type == "tetra":
            tets.append(blk.data)
            ttag.append(tags)
        elif blk.type == "triangle":
            tris.append(blk.data)
            trtag.append(tags)
    names = {k: int(v[0]) for k, v in m.field_data.items()}
    return MshData(
        np.asarray(m.points),
        np.concatenate(tets),
        np.concatenate(ttag),
        np.concatenate(tris) if tris else np.zeros((0, 3), dtype=int),
        np.concatenate(trtag) if trtag else np.zeros(0, dtype=int),
        names,
    )


def _face_keys(tri: np.ndarray) -> np.ndarray:
    return np.sort(tri, axis=1)


def tet_faces(tets: np.ndarray) -> np.ndarray:
    return np.concatenate(
        [tets[:, [a, b, c]] for a, b, c in ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3))]
    )


def boundary_faces(tets: np.ndarray) -> np.ndarray:
    """Sorted node triples of the faces that belong to exactly one tetrahedron."""
    f = _face_keys(tet_faces(tets))
    u, c = np.unique(f, axis=0, return_counts=True)
    assert c.max() <= 2, "a face is shared by more than two tetrahedra (non-manifold mesh)"
    return u[c == 1]


def assert_watertight(tets: np.ndarray) -> None:
    """Conformal (faces shared by <= 2 tets) and closed (every boundary edge shared twice)."""
    bf = boundary_faces(tets)
    edges = np.concatenate([bf[:, [0, 1]], bf[:, [0, 2]], bf[:, [1, 2]]])
    _, c = np.unique(edges, axis=0, return_counts=True)
    assert (c == 2).all(), f"{int((c != 2).sum())} boundary edges are not shared by two faces"


def tet_volume(points: np.ndarray, tets: np.ndarray) -> float:
    a, b, c, d = (points[tets[:, i]] for i in range(4))
    return float(np.abs(np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a)).sum() / 6.0)


def assert_groups_complete(data: MshData, stats: MeshStats) -> None:
    """Every tet is in a 'mat:' group, every exterior face in a surface group, and the written
    triangles are all faces of tets (nothing dangling)."""
    mat_tags = {t for n, t in data.names.items() if n.startswith("mat:")}
    assert set(np.unique(data.tet_tag)) <= mat_tags
    assert len(data.tets) == stats.n_tets
    bf = {tuple(r) for r in boundary_faces(data.tets).tolist()}
    grouped = {tuple(r) for r in _face_keys(data.tris).tolist()}
    missing = bf - grouped
    assert not missing, f"{len(missing)} exterior faces are in no physical group"
    all_faces = {tuple(r) for r in _face_keys(tet_faces(data.tets)).tolist()}
    assert grouped <= all_faces, "a written triangle is not a face of any tetrahedron"
    # surface groups are disjoint: no triangle written twice
    assert len(grouped) == len(data.tris)


def mesh_to(model: OccModel, tmp_path: Path, name: str = "m.msh", **kw: object) -> MeshStats:
    return write_msh(model, tmp_path / name, **kw)  # type: ignore[arg-type]


__all__ = [
    "DjordjevicSarkar",
    "MshData",
    "assert_groups_complete",
    "assert_watertight",
    "boundary_faces",
    "fr4_materials",
    "mesh_to",
    "read_msh",
    "rect_poly",
    "tet_volume",
    "thick_board",
    "with_vias",
]
