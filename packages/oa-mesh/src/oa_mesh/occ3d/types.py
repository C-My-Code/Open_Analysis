# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Types of the 3D OCC mesher (WP-206). Importing this module never loads gmsh."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import Field

from oa_mesh.types import CleanOptions
from oa_model._base import OAModel

__all__ = [
    "GroupInfo",
    "MeshStats",
    "OccModel",
    "OccOptions",
    "OccReport",
    "Prim",
]

# Volume priority when pieces of several primitives coincide after BooleanFragments.
CLS_VOID = 0
CLS_COPPER = 1
CLS_SLAB = 2
CLS_FILL = 3
CLS_BACKGROUND = 4

GroupKind = Literal["material", "conductor", "via", "port", "boundary"]


class OccOptions(OAModel):
    """Knobs of the 3D OCC modeller and of the size field."""

    f_max: float = Field(
        default=10e9, gt=0, description="Highest frequency the mesh must resolve [Hz]."
    )
    barrel_segments: int = Field(
        default=14, ge=12, description="Mesh segments around a via barrel (at least 12)."
    )
    edge_divisions: float = Field(
        default=4.0, gt=0, description="Conductor edges are refined to width / this value."
    )
    thin_elements: int = Field(
        default=2, ge=1, description="Minimum elements across a thin dielectric."
    )
    clean: CleanOptions = Field(
        default=CleanOptions(snap=1e-9, min_feature=1e-6),
        description="Polygon healing; features thinner than min_feature [m] (default 1 um) go.",
    )
    backdrill_clearance: float | None = Field(
        default=None,
        ge=0,
        description=(
            "If set, the span of a via outside its plated range becomes an air-filled void of "
            "radius r_outer + this value [m]; None keeps the dielectric there."
        ),
    )
    heal_tolerance: float = Field(
        default=1e-9,
        gt=0,
        description="OCC healing tolerance used on the retry after a failed boolean [m].",
    )


class GroupInfo(OAModel):
    """One physical group and what it maps back to."""

    name: str = Field(description="Physical group name, e.g. 'mat:FR4', 'cond:L2:GND', 'port:P1'.")
    dim: int = Field(description="Entity dimension: 3 for materials, 2 for surfaces.")
    tag: int = Field(description="Physical tag = the attribute number written to the .msh file.")
    kind: GroupKind = Field(description="What the group represents.")
    model_id: str = Field(
        description="Id in the Structure3D / EMProblem (material, body, via, port name, face)."
    )
    net: str | None = Field(default=None, description="Net of the conductor, if any.")
    role: str | None = Field(default=None, description="Electrical role of the conductor.")
    n_entities: int = Field(default=0, description="Number of geometric entities in the group.")
    measure: float = Field(
        default=0.0, description="Volume [m^3] (dim 3) or area [m^2] (dim 2) in the OCC model."
    )
    representation: str | None = Field(
        default=None, description="'solid' or 'sheet' for conductors."
    )


class OccReport(OAModel):
    """Result of building and fragmenting the model (no meshing yet)."""

    groups: tuple[GroupInfo, ...] = Field(description="All physical groups, ordered by tag.")
    n_volumes: int = Field(description="Volumes in the meshed domain after removing solid copper.")
    domain_volume: float = Field(description="Sum of the meshed volumes [m^3].")
    box_volume: float = Field(description="Volume of the clipped bounding box [m^3].")
    copper_volume: float = Field(description="Volume removed as solid copper [m^3].")
    warnings: tuple[str, ...] = Field(default=(), description="Non-fatal findings.")
    retried_with_healing: bool = Field(
        default=False, description="True if the first boolean failed and healing was applied."
    )

    def group(self, name: str) -> GroupInfo:
        for g in self.groups:
            if g.name == name:
                return g
        raise KeyError(name)


class MeshStats(OAModel):
    """Statistics of a written mesh."""

    path: str = Field(description="Path of the .msh file.")
    msh_version: str = Field(description="gmsh file format version written.")
    gmsh_version: str = Field(description="gmsh version used.")
    n_nodes: int = Field(description="Number of nodes.")
    n_tets: int = Field(description="Number of tetrahedra.")
    n_triangles: int = Field(description="Number of boundary and interface triangles written.")
    n_edges: int = Field(description="Unique tetrahedron edges.")
    n_faces: int = Field(description="Unique tetrahedron faces.")
    dof_p1: int = Field(description="Estimated Nedelec DOFs, order 1: one per edge.")
    dof_p2: int = Field(description="Estimated Nedelec DOFs, order 2: two per edge + two per face.")
    min_sicn: float = Field(description="Smallest signed inverse condition number (1 = ideal).")
    min_gamma: float = Field(description="Smallest inscribed/circumscribed radius ratio.")
    min_volume: float = Field(description="Smallest tetrahedron volume [m^3].")
    tet_volume: float = Field(description="Sum of tetrahedron volumes [m^3].")
    volume_by_group: dict[str, float] = Field(description="Tet volume per 'mat:' group [m^3].")
    h_max: float = Field(description="Largest target size used [m].")
    h_min: float = Field(description="Smallest target size used [m].")
    content_hash: str = Field(description="SHA-256 of node coordinates (1 nm) and connectivity.")
    elapsed_s: float = Field(description="Wall time of write_msh [s].")


@dataclass(frozen=True)
class Prim:
    """One OCC primitive of the plan, in metres.

    ``kind``: ``extrude`` (polygons between z0 and z1), ``sheet`` (polygons at z0), ``cyl``
    (barrel; ``r_inner`` > 0 makes a tube), ``disc`` (circle sheet at z0), ``rect`` (port quad,
    four ``corners``), ``annulus`` (coax port ring at z0).
    """

    kind: str
    key: str
    cls: int
    material: str | None = None
    polys: tuple[Any, ...] = ()
    z0: float = 0.0
    z1: float = 0.0
    center: tuple[float, float] = (0.0, 0.0)
    r: float = 0.0
    r_inner: float = 0.0
    corners: tuple[tuple[float, float, float], ...] = ()


@dataclass(frozen=True)
class ConductorInfo:
    key: str
    kind: Literal["conductor", "via"]
    net: str | None
    role: str | None
    representation: str
    width: float
    radius: float = 0.0


@dataclass(frozen=True)
class PortInfo:
    name: str
    kind: str
    key: str


@dataclass(frozen=True)
class OccModel:
    """Resolved, pure-data description of the OCC model.

    gmsh state cannot outlive a session, so the model holds the *plan* (cleaned polygons, solids,
    sheets, ports, group names and tags); ``write_msh`` and ``export_glb`` replay it inside a
    session. ``report`` comes from a validating build done by ``build_occ_model``.
    """

    structure_key: str
    options: OccOptions
    bounds: tuple[float, float, float, float, float, float]
    prims: tuple[Prim, ...]
    conductors: tuple[ConductorInfo, ...]
    ports: tuple[PortInfo, ...]
    material_models: dict[str, Any] = field(default_factory=dict)
    boundary_bc: dict[str, str] = field(default_factory=dict)
    copper_thicknesses: tuple[float, ...] = ()
    warnings: tuple[str, ...] = ()
    report: OccReport | None = None
