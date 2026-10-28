# SPDX-License-Identifier: GPL-3.0-or-later
"""Thermal problem types, reserved for M2 (interfaces.md section 5.5)."""

from typing import Literal

from pydantic import Field

from oa_model._base import OAModel
from oa_model.common import LayerId, Temperature
from oa_model.em import Structure3D
from oa_model.geometry import Polygon


class HeatSource(OAModel):
    id: str = Field(description="Unique source id.")
    kind: Literal["component", "polygon", "joule_field"] = Field(description="Source type.")
    component: str | None = Field(default=None, description="Refdes: power over the courtyard.")
    polygon: Polygon | None = Field(default=None, description="Source area, board frame.")
    layer: LayerId | None = Field(default=None, description="Layer id of the polygon.")
    power: float = Field(default=0.0, ge=0, description="Dissipated power, watts.")
    theta_jc: float | None = Field(default=None, description="Junction-to-case, K/W.")
    theta_jb: float | None = Field(default=None, description="Junction-to-board, K/W.")


class ThermalBC(OAModel):
    t_ambient: Temperature = Field(default=298.15, description="Ambient temperature, K.")
    h_top: float = Field(default=10.0, ge=0, description="Top convection, W/(m^2 K).")
    h_bottom: float = Field(default=10.0, ge=0, description="Bottom convection, W/(m^2 K).")
    emissivity_top: float | None = Field(default=0.9, ge=0, le=1, description="Top emissivity.")
    emissivity_bottom: float | None = Field(
        default=0.9, ge=0, le=1, description="Bottom emissivity."
    )
    fixed_temperature: tuple[tuple[Polygon, float], ...] = Field(
        default=(), description="(area, temperature in K) pairs: heatsink or cold plate."
    )


class ThermalProblem(OAModel):
    schema: Literal["oa-thermalproblem/1"] = Field(  # type: ignore[assignment]
        default="oa-thermalproblem/1", description="Schema tag; always 'oa-thermalproblem/1'."
    )
    grid: Literal["layered_fvm", "fem3d"] = Field(default="layered_fvm", description="Grid type.")
    cell_size: float = Field(default=0.25e-3, gt=0, description="Raster cell size, metres.")
    sources: tuple[HeatSource, ...] = Field(description="Heat sources.")
    bc: ThermalBC = Field(description="Boundary conditions.")
    structure: Structure3D | None = Field(
        default=None, description="fem3d only; layered_fvm reads the Board directly."
    )
