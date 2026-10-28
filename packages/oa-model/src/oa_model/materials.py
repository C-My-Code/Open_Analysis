# SPDX-License-Identifier: GPL-3.0-or-later
"""Materials, dielectric frequency models and roughness (board-model.md section 6.1), plus the
shipped material library."""

import math
from functools import cache
from importlib import resources
from typing import Annotated, Literal

import yaml
from pydantic import Field, TypeAdapter

from oa_model._base import OAModel
from oa_model.arrays import Floats
from oa_model.common import Frequency, Length

_TWO_PI = 2.0 * math.pi


class ConstantDkDf(OAModel):
    """Constant Dk/Df at f_ref. NOT causal; physics converts it to Djordjevic-Sarkar."""

    kind: Literal["constant"] = Field(
        default="constant", description="Discriminator tag; always 'constant'."
    )
    dk: float = Field(ge=1.0, description="Relative permittivity (dimensionless), >= 1.")
    df: float = Field(ge=0.0, lt=1.0, description="Loss tangent (dimensionless).")
    f_ref: Frequency = Field(
        default=1e9, description="Frequency (Hz) at which dk/df were measured."
    )


class DjordjevicSarkar(OAModel):
    """Wideband causal Debye model defined by a single datasheet point."""

    kind: Literal["djordjevic_sarkar"] = Field(
        default="djordjevic_sarkar", description="Discriminator tag; always 'djordjevic_sarkar'."
    )
    dk: float = Field(ge=1.0, description="Relative permittivity at f_ref (dimensionless).")
    df: float = Field(ge=0.0, lt=1.0, description="Loss tangent at f_ref (dimensionless).")
    f_ref: Frequency = Field(default=1e9, description="Datasheet reference frequency, Hz.")
    f_low: Frequency = Field(
        default=1e4 / _TWO_PI, description="Lower model corner (Hz); m1 = log10(2*pi*f_low)."
    )
    f_high: Frequency = Field(
        default=1e12 / _TWO_PI, description="Upper model corner (Hz); m2 = log10(2*pi*f_high)."
    )


class MultiPoleDebye(OAModel):
    kind: Literal["multipole_debye"] = Field(
        default="multipole_debye", description="Discriminator tag; always 'multipole_debye'."
    )
    eps_inf: float = Field(ge=1.0, description="High-frequency permittivity (dimensionless).")
    poles: tuple[tuple[float, Frequency], ...] = Field(
        description="Pairs (delta_eps_k, f_relax_k in Hz), one per Debye pole."
    )
    sigma_dc: float = Field(default=0.0, ge=0.0, description="DC conductivity, S/m.")


class TableDkDf(OAModel):
    """Measured/vendor table; interpolated log-f, linear Dk/Df."""

    kind: Literal["table"] = Field(
        default="table", description="Discriminator tag; always 'table'."
    )
    freqs: Floats = Field(description="Strictly increasing frequencies, Hz.")
    dk: Floats = Field(description="Relative permittivity at each frequency.")
    df: Floats = Field(description="Loss tangent at each frequency.")


DielectricModel = Annotated[
    ConstantDkDf | DjordjevicSarkar | MultiPoleDebye | TableDkDf, Field(discriminator="kind")
]


class ThermalProps(OAModel):
    """Bulk thermal properties, anisotropic: in-plane (xy) and through-plane (z)."""

    k_xy: float = Field(gt=0.0, description="In-plane thermal conductivity, W/(m K).")
    k_z: float = Field(gt=0.0, description="Through-plane thermal conductivity, W/(m K).")
    density: float | None = Field(default=None, gt=0.0, description="Density, kg/m^3.")
    specific_heat: float | None = Field(
        default=None, gt=0.0, description="Specific heat, J/(kg K)."
    )
    emissivity: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Surface emissivity (0..1), mask surfaces."
    )


class DielectricMaterial(OAModel):
    kind: Literal["dielectric"] = Field(
        default="dielectric", description="Discriminator tag; always 'dielectric'."
    )
    name: str = Field(
        description="Material key referenced by stack layers.", examples=["FR4_generic"]
    )
    model: DielectricModel = Field(description="Frequency-dependent Dk/Df model.")
    dk_z_over_dk_xy: float = Field(
        default=1.0, gt=0.0, description="Anisotropy ratio Dk_z / Dk_xy; 1.0 means isotropic."
    )
    thermal: ThermalProps | None = Field(default=None, description="Thermal properties, if known.")
    source: Literal["ingest", "sidecar", "library", "default"] = Field(
        default="ingest", description="Where this material definition came from."
    )


class ConductorMaterial(OAModel):
    kind: Literal["conductor"] = Field(
        default="conductor", description="Discriminator tag; always 'conductor'."
    )
    name: str = Field(description="Material key, e.g. 'copper_ed'.", examples=["copper_ed"])
    conductivity: float = Field(default=5.8e7, gt=0.0, description="Conductivity at t_ref, S/m.")
    t_ref: float = Field(default=293.15, description="Reference temperature for conductivity, K.")
    temp_coeff: float = Field(
        default=3.93e-3,
        description="Resistivity temperature coefficient, 1/K: rho(T)=rho0(1+a(T-t_ref)).",
    )
    mu_r: float = Field(default=1.0, gt=0.0, description="Relative permeability (dimensionless).")
    thermal: ThermalProps | None = Field(
        default=ThermalProps(k_xy=390.0, k_z=390.0), description="Thermal properties."
    )
    source: Literal["ingest", "sidecar", "library", "default"] = Field(
        default="default", description="Where this material definition came from."
    )


Material = Annotated[DielectricMaterial | ConductorMaterial, Field(discriminator="kind")]


class NoRoughness(OAModel):
    kind: Literal["none"] = Field(default="none", description="Discriminator tag; always 'none'.")


class Hammerstad(OAModel):
    kind: Literal["hammerstad"] = Field(
        default="hammerstad", description="Discriminator tag; always 'hammerstad'."
    )
    rq: Length = Field(description="RMS roughness, metres.")


class Groisse(OAModel):
    kind: Literal["groisse"] = Field(
        default="groisse", description="Discriminator tag; always 'groisse'."
    )
    rq: Length = Field(description="RMS roughness, metres.")


class Huray(OAModel):
    kind: Literal["huray"] = Field(
        default="huray", description="Discriminator tag; always 'huray'."
    )
    sphere_radius: Length = Field(description="Nodule sphere radius a, metres.")
    surface_ratio: float = Field(
        gt=0.0, description="N*4*pi*a^2 / A_flat summed over one size class (dimensionless)."
    )
    flat_ratio: float = Field(default=1.0, gt=0.0, description="A_matte / A_flat (dimensionless).")


class CannonballHuray(OAModel):
    kind: Literal["cannonball_huray"] = Field(
        default="cannonball_huray", description="Discriminator tag; always 'cannonball_huray'."
    )
    rz: Length = Field(description="Datasheet Rz (10-point height), metres.")


Roughness = Annotated[
    NoRoughness | Hammerstad | Groisse | Huray | CannonballHuray, Field(discriminator="kind")
]


class MaterialLibraryEntry(OAModel):
    """One library record: a material plus its citation."""

    citation: str = Field(description="Where the values come from; flags values to verify.")
    material: Material = Field(description="The material definition.")


class _LibraryFile(OAModel):
    schema_: Literal["oa-materials/1"] = Field(alias="schema")
    entries: tuple[MaterialLibraryEntry, ...]


@cache
def library_entries() -> tuple[MaterialLibraryEntry, ...]:
    """Load the shipped material library (`data/materials.yaml`)."""
    text = (resources.files("oa_model") / "data" / "materials.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    return TypeAdapter(_LibraryFile).validate_python(data).entries


def library_material(name: str) -> DielectricMaterial | ConductorMaterial | None:
    """Look up a library material by name (returned with source='library'), or None."""
    for entry in library_entries():
        if entry.material.name == name:
            return entry.material.model_copy(update={"source": "library"})
    return None
