# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``pi.dc`` configuration (interfaces.md section 6, WP-204)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from oa_model._base import OAModel
from oa_model.common import Length, NetId, Temperature
from oa_model.units import quantity

__all__ = [
    "Current",
    "DcConfig",
    "LimitsConfig",
    "LoadConfig",
    "MeshConfig",
    "RailConfig",
    "SenseConfig",
    "VoltageV",
    "VrmConfig",
]

Current = Annotated[
    float,
    Field(description="Current in amperes. Strings such as '12A' or '500mA' parse."),
    BeforeValidator(quantity("current")),
]
VoltageV = Annotated[
    float,
    Field(description="Voltage in volts. Strings such as '1.0V' or '900mV' parse."),
    BeforeValidator(quantity("voltage")),
]
_ROOM = 298.15


class RailConfig(OAModel):
    net: NetId = Field(description="Supply net whose copper carries the load current.")
    return_: NetId | None = Field(
        default=None,
        alias="return",
        description=(
            "Return (ground) net, solved together with the rail. None = ideal lossless return: "
            "the drop is then the rail copper only."
        ),
    )


class SenseConfig(OAModel):
    pos: str = Field(description="Remote-sense pin on the rail net, 'REFDES.PIN' (e.g. 'U1.AA12').")
    neg: str | None = Field(
        default=None,
        description="Remote-sense pin on the return net, 'REFDES.PIN'. None = sense to ground.",
    )


class VrmConfig(OAModel):
    component: str = Field(description="Refdes of the regulator (or connector) feeding the rail.")
    pins: tuple[str, ...] = Field(
        default=(),
        description="Output pin numbers on the rail net; empty = every pin of the component on it.",
    )
    return_pins: tuple[str, ...] = Field(
        default=(),
        description="Return pin numbers on the return net; empty = every pin on the return net.",
    )
    vset: VoltageV = Field(
        default=1.0, description="Regulated output voltage [V] (between output and return pins)."
    )
    r_out: float = Field(
        default=0.0, ge=0.0, description="Thevenin output resistance of the regulator [ohm]."
    )
    sense: SenseConfig | None = Field(
        default=None,
        description="Remote sense: the regulator adjusts its output so the sensed voltage is vset.",
    )


class LoadConfig(OAModel):
    component: str = Field(description="Refdes of the load component (IC, connector).")
    nets: tuple[NetId, ...] = Field(
        default=(),
        description="Nets drawing the current; the first must be the rail net. Empty = the rail.",
    )
    pins: tuple[str, ...] = Field(
        default=(), description="Pin numbers on the rail net; empty = every pin on the rail net."
    )
    return_pins: tuple[str, ...] = Field(
        default=(), description="Pin numbers on the return net; empty = every pin on it."
    )
    current: Current = Field(description="Total load current [A], drawn from the rail pins.")
    model: Literal["pin_group", "equal_split"] = Field(
        default="pin_group",
        description=(
            "'pin_group': all pins are one equipotential node and the copper decides the split "
            "(default). 'equal_split': every pin draws current/N independently."
        ),
    )
    name: str | None = Field(default=None, description="Display name; defaults to the refdes.")


class MeshConfig(OAModel):
    h_plane: Length = Field(
        default=0.8e-3, gt=0.0, description="Target element edge in copper areas [m]."
    )
    h_trace_factor: float = Field(
        default=0.33,
        gt=0.0,
        le=1.0,
        description="Element edge along tracks as a fraction of the track width [1].",
    )
    via_ring_segments: int = Field(
        default=16, ge=8, le=256, description="Mesh nodes around every via/pad contact ring [1]."
    )
    solver: Literal["auto", "superlu", "pardiso", "cholmod", "amg"] = Field(
        default="auto",
        description=(
            "Linear solver. 'auto': PARDISO or CHOLMOD when installed, else SuperLU; AMG-PCG "
            "above 1 million unknowns."
        ),
    )


class LimitsConfig(OAModel):
    drop_fraction: float = Field(
        default=0.03, gt=0.0, lt=1.0, description="Allowed load drop as a fraction of vset [1]."
    )
    via_current_density: float = Field(
        default=3e8, gt=0.0, description="Allowed via barrel current density [A/m^2]."
    )


class DcConfig(OAModel):
    rail: RailConfig = Field(description="Supply and return nets.")
    vrm: VrmConfig = Field(description="The regulator (Dirichlet source).")
    loads: tuple[LoadConfig, ...] = Field(min_length=1, description="Current sinks.")
    temperature: Temperature = Field(
        default=_ROOM, description="Uniform copper temperature [K]. Strings such as '25degC' parse."
    )
    mesh: MeshConfig = Field(default=MeshConfig(), description="Meshing and solver controls.")
    limits: LimitsConfig = Field(default=LimitsConfig(), description="Pass/fail limits.")
