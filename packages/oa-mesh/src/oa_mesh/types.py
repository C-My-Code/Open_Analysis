# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Mesh input/output types (interfaces.md section 9). Importing this module never loads gmsh."""

from __future__ import annotations

import hashlib
from typing import Literal

import numpy as np
import numpy.typing as npt
from pydantic import Field, model_validator

from oa_model._base import OAModel
from oa_model.arrays import NDArray, Points
from oa_model.common import ItemId, LayerId, NetId
from oa_model.geometry import Polygon
from oa_results.fields import TriMesh2D

__all__ = [
    "CleanOptions",
    "CleanReport",
    "Contact",
    "ContactNodes",
    "EdgeSet",
    "MeshQuality",
    "PhysicalGroup",
    "SheetMesh",
    "SheetPiece",
    "SizeField",
    "XsMesh",
    "mesh_digest",
]


def mesh_digest(
    nodes: npt.NDArray[np.float64], cells: npt.NDArray[np.int64], resolution: float
) -> str:
    """SHA-256 over the node coordinates rounded to ``resolution`` and the cell connectivity."""
    q = np.floor(nodes / resolution + 0.5).astype("<i8")
    h = hashlib.sha256()
    h.update(np.asarray(nodes.shape, dtype="<i8").tobytes())
    h.update(np.ascontiguousarray(q).tobytes())
    h.update(np.asarray(cells.shape, dtype="<i8").tobytes())
    h.update(np.ascontiguousarray(cells.astype("<i8")).tobytes())
    return h.hexdigest()


class CleanOptions(OAModel):
    """Shapely pre-processing applied to every input polygon before gmsh sees it."""

    snap: float = Field(
        default=1e-10,
        gt=0,
        description=(
            "Coordinate grid that all vertices are snapped to [m]. 0.1 nm, finer than the 1 nm "
            "board grid."
        ),
    )
    min_feature: float = Field(
        default=1e-6,
        ge=0,
        description=(
            "Copper spurs/slivers, holes and slits narrower than this are removed or closed [m]. "
            "Always at least 4 x snap, so hole-touches-outline pinches are resolved. 1-5 um is "
            "typical for real boards."
        ),
    )
    simplify: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Douglas-Peucker tolerance applied last [m]; 0 disables. 1-5 um merges near-collinear "
            "vertices but changes areas by up to tolerance x perimeter."
        ),
    )


class CleanReport(OAModel):
    """What cleaning did to one piece."""

    area_in: float = Field(description="Union area of the input polygons [m^2].")
    area_out: float = Field(description="Area of the cleaned geometry that was meshed [m^2].")
    n_polygons: int = Field(description="Polygons (connected copper islands) after cleaning [1].")
    n_holes: int = Field(description="Total hole count after cleaning [1].")
    n_repaired: int = Field(description="Input polygons that needed make_valid repair [1].")
    n_dropped: int = Field(description="Input parts dropped (degenerate/line/point results) [1].")
    removed_area: float = Field(
        description="Copper removed by sliver/spur opening and island removal [m^2]."
    )
    closed_area: float = Field(description="Gap area filled by closing narrow slits [m^2].")
    warnings: tuple[str, ...] = Field(default=(), description="Human-readable notes.")


class MeshQuality(OAModel):
    """Triangle quality computed on the corner nodes (P2 mid-nodes ignored)."""

    n_nodes: int = Field(description="Node count [1].")
    n_triangles: int = Field(description="Triangle count [1].")
    min_angle_deg: float = Field(description="Smallest triangle angle [deg].")
    max_angle_deg: float = Field(description="Largest triangle angle [deg].")
    max_aspect: float = Field(
        description="Worst circumradius/(2 x inradius) ratio, 1 for equilateral [1]."
    )
    min_edge: float = Field(description="Shortest triangle edge [m].")
    max_edge: float = Field(description="Longest triangle edge [m].")
    area: float = Field(description="Total mesh area [m^2].")


class PhysicalGroup(OAModel):
    """A gmsh physical group and the board-model / cross-section id it came from."""

    name: str = Field(description="Group name, e.g. 'conductor:s1', 'dielectric:0', 'contact:v1'.")
    dim: int = Field(description="Topological dimension: 0 point, 1 curve, 2 surface [1].")
    tag: int = Field(description="gmsh physical tag, unique per dimension [1].")
    kind: str = Field(description="'conductor', 'conductor_interior', 'dielectric', 'air', ...")
    source_id: str | None = Field(
        default=None,
        description="Id in the input (conductor id, dielectric index, piece id, contact id).",
    )


class EdgeSet(OAModel):
    """Boundary edges carrying one tag."""

    name: str = Field(description="Physical group name the edges belong to.")
    source_id: str | None = Field(default=None, description="Conductor id, or None for 'outer'.")
    edges: NDArray = Field(
        description="(K, 2) int64 node pairs, or (K, 3) [n0, n1, mid] for P2 meshes [1]."
    )


class XsMesh(OAModel):
    """2D FEM mesh of a ``CrossSection`` in the section's (u, z) frame.

    Conductors are holes of the meshed domain (their boundaries are tagged) unless
    ``mesh_conductors=True`` was requested, in which case their interiors are meshed too
    (region tag ``-2 - conductor_index``).
    """

    order: Literal[1, 2] = Field(description="Element order: 1 = P1 (3 nodes), 2 = P2 (6 nodes).")
    nodes: Points = Field(description="(N, 2) node coordinates (u, z) [m].")
    triangles: NDArray = Field(
        description="(M, 3) or (M, 6) int64; P2 order is [v0, v1, v2, m01, m12, m20] [1]."
    )
    region: NDArray = Field(
        description="(M,) int64 per triangle: dielectric index >= 0, -1 air, <= -2 conductor [1]."
    )
    conductor_edges: tuple[EdgeSet, ...] = Field(
        description="Boundary edges per conductor, in CrossSection.conductors order."
    )
    outer_edges: EdgeSet = Field(description="Edges of the outer truncation box.")
    groups: tuple[PhysicalGroup, ...] = Field(description="Physical-group table (tag -> id).")
    quality: MeshQuality = Field(description="Quality metrics of the mesh.")
    gmsh_version: str = Field(description="gmsh version that produced the mesh.")
    box: tuple[float, float, float, float] = Field(
        description="Outer box (u_min, z_min, u_max, z_max) [m]."
    )
    warnings: tuple[str, ...] = Field(default=(), description="Non-fatal notes.")

    @model_validator(mode="after")
    def _check(self) -> XsMesh:
        t = self.triangles.array
        if t.ndim != 2 or t.shape[1] != 3 * self.order:
            raise ValueError("triangles shape does not match order")
        if self.region.array.shape != (t.shape[0],):
            raise ValueError("region must have one entry per triangle")
        return self

    def content_hash(self) -> str:
        """Hash of node coordinates (rounded to 1e-12 x box size) and connectivity."""
        res = 1e-12 * max(self.box[2] - self.box[0], self.box[3] - self.box[1])
        return mesh_digest(self.nodes.array, self.triangles.array, res)

    def conductor_edge_set(self, conductor_id: str) -> EdgeSet:
        for e in self.conductor_edges:
            if e.source_id == conductor_id:
                return e
        raise KeyError(conductor_id)


class Contact(OAModel):
    """A via/pad contact in a copper piece: a ring (or a point when ``radius == 0``)."""

    id: ItemId = Field(description="Board-model id of the via/pad (traceability key).")
    x: float = Field(description="Centre x in the board frame [m].")
    y: float = Field(description="Centre y in the board frame [m].")
    radius: float = Field(
        default=0.0, ge=0, description="Ring radius [m]; 0 embeds a single point contact."
    )
    segments: int | None = Field(
        default=None,
        ge=8,
        le=1024,
        description="Ring vertex count [1]; None picks max(16, ring circumference / local size).",
    )
    mode: Literal["embedded", "hole"] = Field(
        default="embedded",
        description=(
            "'embedded': the ring is an embedded curve and the disc stays meshed copper; "
            "'hole': the disc is removed and the ring is a boundary."
        ),
    )


class SheetPiece(OAModel):
    """One copper polygon set on one layer and net, with its contacts."""

    id: str = Field(description="Unique piece id, e.g. 'L2:+1V0'. Traces every output back.")
    layer: LayerId | None = Field(default=None, description="Copper layer id.")
    net: NetId | None = Field(default=None, description="Net name.")
    z: float | None = Field(default=None, description="Layer z [m], carried to the output.")
    polygons: tuple[Polygon, ...] = Field(
        description="Copper polygons with holes in the board frame [m]; may overlap or be invalid."
    )
    contacts: tuple[Contact, ...] = Field(default=(), description="Via/pad contacts.")


class SizeField(OAModel):
    """A mesh-size constraint: ``size`` at the feature, growing by ``grading`` away from it."""

    kind: Literal["contacts", "points", "polyline", "boundary"] = Field(
        description=(
            "'contacts': ring vertices of the contacts; 'points': explicit points; "
            "'polyline': along a path (e.g. a trace centre line, size = width/3); "
            "'boundary': along the piece outlines and holes."
        )
    )
    size: float = Field(gt=0, description="Element size at the feature [m].")
    grading: float = Field(
        default=1.3, gt=1.0, le=2.0, description="Size growth per unit distance + 1 [1]."
    )
    contact_ids: tuple[str, ...] | None = Field(
        default=None, description="For 'contacts': restrict to these ids; None = all."
    )
    points: Points | None = Field(
        default=None, description="For 'points'/'polyline': (K, 2) coordinates [m]."
    )
    piece_ids: tuple[str, ...] | None = Field(
        default=None, description="Restrict to these pieces; None = all."
    )


class ContactNodes(OAModel):
    """Mesh nodes of one contact."""

    id: str = Field(description="Contact id from the input.")
    nodes: NDArray = Field(
        description="int64 node indices, ring order (closed ring, not repeated)."
    )
    radial_error: float = Field(
        description=(
            "Max over the ring nodes of | |node - centre| - radius | [m]; 0 for point contacts."
        )
    )


class SheetMesh(TriMesh2D):
    """``TriMesh2D`` of one copper piece plus the contact -> node map and provenance."""

    piece_id: str = Field(description="SheetPiece.id this mesh came from.")
    net: NetId | None = Field(default=None, description="Net of the piece.")
    contacts: tuple[ContactNodes, ...] = Field(description="Node indices per contact, input order.")
    groups: tuple[PhysicalGroup, ...] = Field(description="Physical-group table (tag -> id).")
    quality: MeshQuality = Field(description="Quality metrics of the mesh.")
    cleaning: CleanReport = Field(description="What shapely cleaning changed.")
    gmsh_version: str = Field(description="gmsh version that produced the mesh.")

    def contact_nodes(self, contact_id: str) -> npt.NDArray[np.int64]:
        for c in self.contacts:
            if c.id == contact_id:
                return np.asarray(c.nodes.array, dtype=np.int64)
        raise KeyError(contact_id)

    def content_hash(self) -> str:
        n = self.nodes.array
        span = float(np.max(np.ptp(n, axis=0))) if len(n) else 1.0
        return mesh_digest(n, self.triangles.array, 1e-12 * max(span, 1e-12))
