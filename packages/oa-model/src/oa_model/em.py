# SPDX-License-Identifier: GPL-3.0-or-later
"""Solver-neutral EM problem description (interfaces.md section 5). All lengths in metres."""

from typing import Annotated, Literal

from pydantic import Field, JsonValue

from oa_model._base import OAModel
from oa_model._canon import canon, sha256_json
from oa_model.common import Angle, Frequency, LayerId, NetId, Vec2
from oa_model.geometry import Polygon
from oa_model.materials import Material, NoRoughness, Roughness

_Role = Literal["signal", "reference", "floating"]


class Box3(OAModel):
    xmin: float = Field(description="Metres.")
    ymin: float = Field(description="Metres.")
    zmin: float = Field(description="Metres.")
    xmax: float = Field(description="Metres.")
    ymax: float = Field(description="Metres.")
    zmax: float = Field(description="Metres.")


class MaterialSlab(OAModel):
    """Dielectric layer clipped to the region."""

    material: str = Field(description="Name of a material in Structure3D.materials.")
    z_bottom: float = Field(description="Bottom z, metres.")
    z_top: float = Field(description="Top z, metres.")
    footprint: Polygon | None = Field(
        default=None, description="XY footprint; None = full region rectangle."
    )


class ConductorBody(OAModel):
    """Extruded copper: polygons (with holes, e.g. antipads) on one conductor layer."""

    id: str = Field(description="Unique body id.")
    net: NetId | None = Field(description="Net name, or None.")
    role: _Role = Field(description="Electrical role for the solver.")
    layer: LayerId = Field(description="Conductor layer id.")
    z_bottom: float = Field(description="Bottom z, metres.")
    z_top: float = Field(description="Top z, metres.")
    polygons: tuple[Polygon, ...] = Field(description="XY polygons of the copper, metres.")
    material: str = Field(default="copper", description="Conductor material name.")
    roughness: Roughness = Field(default=NoRoughness(), description="Surface roughness.")
    representation: Literal["solid", "sheet", "auto"] = Field(
        default="auto",
        description="'auto': adapter picks a zero-thickness sheet when thinner than a cell.",
    )


class ViaPad(OAModel):
    """A pad disc of a via at one conductor layer (snapping hint for FDTD)."""

    layer: LayerId = Field(description="Conductor layer id.")
    radius: float = Field(gt=0, description="Pad radius, metres.")
    z_bottom: float = Field(description="Bottom z, metres.")
    z_top: float = Field(description="Top z, metres.")


class ViaBody(OAModel):
    id: str = Field(description="Unique body id.")
    net: NetId | None = Field(description="Net name, or None.")
    role: _Role = Field(description="Electrical role.")
    center: Vec2 = Field(description="XY centre, metres.")
    z_bottom: float = Field(description="Plated span bottom after backdrill, metres.")
    z_top: float = Field(description="Plated span top, metres.")
    r_outer: float = Field(gt=0, description="Drill radius, metres.")
    plating: float = Field(ge=0, description="Plating thickness, metres; >= r_outer means filled.")
    fill_material: str | None = Field(default=None, description="Conductive fill or None.")
    pads: tuple[ViaPad, ...] = Field(default=(), description="Pad discs (hints).")


class EMTerminalGeom(OAModel):
    """A conductor contact as geometry: a small face/edge on a conductor surface."""

    position: Vec2 = Field(description="XY, metres.")
    z: float = Field(description="Height, metres.")
    layer: LayerId | None = Field(description="Conductor layer id, if on a layer.")
    extent: tuple[float, float] = Field(
        default=(0.0, 0.0), description="Contact patch size along u, v, metres."
    )


class LumpedElement(OAModel):
    """SMD R/L/C placeholder between two terminals."""

    id: str = Field(description="Unique id.")
    refdes: str | None = Field(default=None, description="Component refdes.")
    positive: EMTerminalGeom = Field(description="Positive terminal.")
    negative: EMTerminalGeom = Field(description="Negative terminal.")
    r: float | None = Field(default=None, description="Resistance, ohms.")
    l: float | None = Field(default=None, description="Inductance, henries.")  # noqa: E741
    c: float | None = Field(default=None, description="Capacitance, farads.")
    topology: Literal["series", "parallel"] = Field(default="series", description="RLC topology.")


class RegionProvenance(OAModel):
    """Where a Structure3D came from. Excluded from the structure key."""

    board_hash: str | None = Field(default=None, description="content_hash of the source board.")
    nets: tuple[NetId, ...] = Field(default=(), description="Signal nets of interest.")
    bbox: tuple[float, float, float, float] | None = Field(
        default=None, description="xmin, ymin, xmax, ymax of the cut, metres."
    )
    region: dict[str, JsonValue] = Field(
        default={}, description="The RegionSpec used, as plain JSON."
    )
    cut_rules: tuple[str, ...] = Field(default=(), description="Names of the cut rules applied.")


class Structure3D(OAModel):
    schema: Literal["oa-structure/1"] = Field(  # type: ignore[assignment]
        default="oa-structure/1", description="Schema tag; always 'oa-structure/1'."
    )
    bbox: Box3 = Field(description="Region of interest, board frame.")
    materials: tuple[Material, ...] = Field(description="Materials used by slabs and bodies.")
    slabs: tuple[MaterialSlab, ...] = Field(description="Dielectric slabs.")
    conductors: tuple[ConductorBody, ...] = Field(description="Copper bodies.")
    vias: tuple[ViaBody, ...] = Field(description="Via bodies.")
    lumped: tuple[LumpedElement, ...] = Field(default=(), description="Lumped components.")
    background: str = Field(default="air", description="Material outside the board.")
    provenance: RegionProvenance = Field(
        default=RegionProvenance(), description="Origin; excluded from key()."
    )

    def key(self) -> str:
        """Canonical sha256 (1 nm rounding, 6 significant digits for materials); excludes
        provenance."""
        dump = self.model_dump(mode="python", exclude={"provenance"})
        return "st1:" + sha256_json(canon(dump, nm=True, sig_keys=frozenset({"materials"})))[:32]


class EMLumpedPort(OAModel):
    kind: Literal["lumped"] = Field(
        default="lumped", description="Discriminator tag; always 'lumped'."
    )
    name: str = Field(description="Port name.")
    positive: EMTerminalGeom = Field(description="Positive terminal.")
    negative: EMTerminalGeom = Field(description="Negative terminal.")
    width: float = Field(gt=0, description="Current sheet width, metres.")
    z0: float = Field(default=50.0, gt=0, description="Reference impedance, ohms.")
    excite: bool = Field(default=True, description="Excited in the sweep.")


class EMWavePort(OAModel):
    """Axis-aligned rectangular face on the region boundary."""

    kind: Literal["wave"] = Field(default="wave", description="Discriminator tag; always 'wave'.")
    name: str = Field(description="Port name.")
    face: Literal["xmin", "xmax", "ymin", "ymax"] = Field(description="Boundary face.")
    rect: tuple[float, float, float, float] = Field(
        description="(u_min, u_max, z_min, z_max) on that face, metres."
    )
    n_modes: int = Field(default=1, ge=1, description="Number of modes.")
    line_type: Literal["microstrip", "stripline", "cpw", "cpwg", "general"] = Field(
        default="general", description="Line type hint for TL-probe adapters."
    )
    signal_conductors: tuple[str, ...] = Field(description="ConductorBody ids crossing the face.")
    z0: float | None = Field(default=None, description="Ohms; None = renormalize to modal Z0.")
    deembed: float = Field(
        default=0.0, description="Reference-plane shift into the structure, metres."
    )
    excite: bool = Field(default=True, description="Excited in the sweep.")


class EMCoaxPort(OAModel):
    kind: Literal["coax"] = Field(default="coax", description="Discriminator tag; always 'coax'.")
    name: str = Field(description="Port name.")
    center: Vec2 = Field(description="XY centre, metres.")
    z: float = Field(description="Port plane z, metres.")
    r_inner: float = Field(gt=0, description="Inner radius, metres.")
    r_outer: float = Field(gt=0, description="Outer radius, metres.")
    dielectric: str = Field(description="Material name between conductors.")
    direction: Literal["+z", "-z"] = Field(description="Propagation direction into the structure.")
    z0: float = Field(default=50.0, gt=0, description="Reference impedance, ohms.")
    excite: bool = Field(default=True, description="Excited in the sweep.")


EMPort = Annotated[EMLumpedPort | EMWavePort | EMCoaxPort, Field(discriminator="kind")]

BC = Literal["pec", "pmc", "absorbing", "pml"]


class Boundaries(OAModel):
    xmin: BC = Field(description="Boundary condition on the -x face.")
    xmax: BC = Field(description="Boundary condition on the +x face.")
    ymin: BC = Field(description="Boundary condition on the -y face.")
    ymax: BC = Field(description="Boundary condition on the +y face.")
    zmin: BC = Field(description="Boundary condition on the -z face.")
    zmax: BC = Field(description="Boundary condition on the +z face.")
    air_padding: float | None = Field(
        default=None, description="Extra air around bbox for open faces, metres; None = lambda/4."
    )


class FrequencyPlan(OAModel):
    f_min: float = Field(ge=0, description="Hz; 0 = adapter's lowest valid point.")
    f_max: Frequency = Field(description="Upper frequency, Hz.")
    n_points: int = Field(default=401, ge=2, description="Number of sweep points.")
    spacing: Literal["linear", "log"] = Field(default="linear", description="Point spacing.")
    adaptive: bool = Field(default=False, description="FEM fast sweep.")
    extra_points: tuple[Frequency, ...] = Field(default=(), description="Extra points, Hz.")


class MeshSettings(OAModel):
    preset: Literal["coarse", "medium", "fine", "expert"] = Field(
        default="medium", description="Mesh quality preset."
    )
    cells_per_wavelength: float | None = Field(
        default=None, gt=0, description="FDTD: coarse 10, medium 20, fine 30."
    )
    max_grading: float = Field(default=1.3, gt=1.0, description="Max neighbour cell-size ratio.")
    min_cell: float | None = Field(default=None, gt=0, description="Minimum cell size, metres.")
    fem_order: int = Field(default=2, ge=1, description="FEM polynomial order.")
    amr_max_passes: int = Field(default=0, ge=0, description="FEM adaptive passes; 0 = none.")
    expert: dict[str, JsonValue] = Field(
        default={}, description="Adapter-specific overrides, namespaced: {'openems.x': ...}."
    )


class Convergence(OAModel):
    energy_decay_db: float = Field(default=-50.0, description="FDTD end criterion, dB.")
    max_timesteps: int = Field(default=1_000_000, ge=1, description="FDTD time-step cap.")
    delta_s: float = Field(default=0.02, gt=0, description="FEM AMR target |delta S|.")
    max_wall_s: float | None = Field(default=None, gt=0, description="Wall-clock limit, seconds.")


class FarFieldRequest(OAModel):
    frequencies: tuple[Frequency, ...] = Field(description="Far-field frequencies, Hz.")
    theta_step: Angle = Field(default=0.0872664626, description="Theta step, radians (5 deg).")
    phi_step: Angle = Field(default=0.0872664626, description="Phi step, radians (5 deg).")


class FieldDumpRequest(OAModel):
    quantity: Literal["E", "H", "J"] = Field(description="Field quantity to dump.")
    frequencies: tuple[Frequency, ...] = Field(description="Dump frequencies, Hz.")
    plane: Literal["xy", "xz", "yz", "volume"] = Field(description="Plane or full volume.")
    position: float | None = Field(
        default=None, description="Plane offset along its normal, metres; None for volume."
    )


class OutputRequest(OAModel):
    sparams: bool = Field(default=True, description="Produce S-parameters.")
    farfield: FarFieldRequest | None = Field(default=None, description="Far-field request.")
    field_dumps: tuple[FieldDumpRequest, ...] = Field(
        default=(), description="Field dumps (bounded size)."
    )
    port_impedance: bool = Field(default=True, description="Modal Z0 per port vs frequency.")


class FullWaveDriven(OAModel):
    kind: Literal["fullwave_driven"] = Field(
        default="fullwave_driven", description="Discriminator tag; always 'fullwave_driven'."
    )


class Eigenmode(OAModel):
    kind: Literal["eigenmode"] = Field(
        default="eigenmode", description="Discriminator tag; always 'eigenmode'."
    )
    n_modes: int = Field(default=10, ge=1, description="Number of modes.")
    f_target: Frequency = Field(description="Search target frequency, Hz.")


class Electrostatic(OAModel):
    kind: Literal["electrostatic"] = Field(
        default="electrostatic", description="Discriminator tag; always 'electrostatic'."
    )


class Magnetostatic(OAModel):
    kind: Literal["magnetostatic"] = Field(
        default="magnetostatic", description="Discriminator tag; always 'magnetostatic'."
    )


Physics = Annotated[
    FullWaveDriven | Eigenmode | Electrostatic | Magnetostatic, Field(discriminator="kind")
]


class EMProblem(OAModel):
    schema: Literal["oa-emproblem/1"] = Field(  # type: ignore[assignment]
        default="oa-emproblem/1", description="Schema tag; always 'oa-emproblem/1'."
    )
    name: str = Field(description="Problem name; excluded from key().")
    structure: Structure3D = Field(description="Geometry and materials.")
    physics: Physics = Field(default=FullWaveDriven(), description="Physics to solve.")
    ports: tuple[EMPort, ...] = Field(description="Ports.")
    boundaries: Boundaries = Field(description="Boundary conditions.")
    frequency: FrequencyPlan = Field(description="Frequency plan.")
    mesh: MeshSettings = Field(default=MeshSettings(), description="Mesh settings.")
    convergence: Convergence = Field(default=Convergence(), description="Convergence criteria.")
    outputs: OutputRequest = Field(default=OutputRequest(), description="Requested outputs.")

    def key(self) -> str:
        """Hash of the canonical form excluding `name`; the structure enters via its own key."""
        dump = self.model_dump(mode="python", exclude={"name", "structure"})
        tree = {"rest": canon(dump), "structure": self.structure.key()}
        return "em1:" + sha256_json(tree)[:32]
