# SPDX-License-Identifier: GPL-3.0-or-later
"""Common scalar types, SourceRef and Diagnostic (board-model.md section 4)."""

from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, StringConstraints

from oa_model._base import OAModel
from oa_model.units import quantity

Length = Annotated[
    float,
    Field(ge=0.0, description="Length in metres, >= 0. Strings such as '5mil' or '0.2mm' parse."),
    BeforeValidator(quantity("length")),
]
Coord = Annotated[
    float,
    Field(description="Signed coordinate in metres. Strings such as '1.5mm' parse."),
    BeforeValidator(quantity("length")),
]
Angle = Annotated[
    float,
    Field(
        description="Angle in radians, counter-clockwise from +X. Strings such as '60deg' parse."
    ),
    BeforeValidator(quantity("angle")),
]
Frequency = Annotated[
    float,
    Field(gt=0.0, description="Frequency in hertz, > 0. Strings such as '1GHz' parse."),
    BeforeValidator(quantity("frequency")),
]
Resistance = Annotated[
    float,
    Field(description="Resistance or impedance in ohms. Strings such as '50ohm' parse."),
    BeforeValidator(quantity("resistance")),
]
Time = Annotated[
    float,
    Field(description="Time in seconds. Strings such as '100ps' parse."),
    BeforeValidator(quantity("time")),
]
Fraction = Annotated[
    float,
    Field(description="Dimensionless fraction (0.05 = 5 %). Strings such as '5%' parse."),
    BeforeValidator(quantity("fraction")),
]
Temperature = Annotated[
    float,
    Field(description="Temperature in kelvin. Strings such as '25degC' parse (= 298.15)."),
    BeforeValidator(quantity("temperature")),
]
Vec2 = tuple[float, float]

ItemId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=200),
    Field(
        description="Stable unique item id: 'k:<uuid>' (KiCad), 'i:<id>' (IPC-2581), 'h:<hash>'."
    ),
]
NetId = Annotated[
    str,
    StringConstraints(min_length=1),
    Field(description="Net name, unique per board (the net's key). Never an index."),
]
LayerId = Annotated[
    str,
    StringConstraints(pattern=r"^(L[1-9][0-9]*|D[1-9][0-9]*|MT|MB)$"),
    Field(
        description=(
            "Stack layer id: L1..Ln copper (top to bottom), D1..Dm dielectric (top to bottom), "
            "MT/MB top/bottom solder mask."
        ),
        examples=["L1", "D2", "MT"],
    ),
]
Side = Literal["top", "bottom"]


class SourceRef(OAModel):
    """Back-reference to the originating item, for cross-probing and diagnostics."""

    tool: Literal["kicad", "ipc2581", "altium", "odbpp", "synthetic", "user"] = Field(
        description="Tool that produced the item."
    )
    uuid: str | None = Field(default=None, description="Source id (KiCad uuid, IPC-2581 id).")
    token: str | None = Field(default=None, description="Source token, e.g. 'segment', 'zone'.")
    name: str | None = Field(default=None, description="Original layer/net/item name.")


class Diagnostic(OAModel):
    """A structured finding. Machine-readable: code, severity, path and hint."""

    severity: Literal["info", "warning", "error"] = Field(description="Severity level.")
    code: str = Field(
        description="Stable code, e.g. 'MODEL.DANGLING_REF' or 'INGEST.ZONE_UNFILLED'.",
        examples=["MODEL.DANGLING_REF"],
    )
    message: str = Field(description="Human-readable description of the problem.")
    item: ItemId | None = Field(default=None, description="Id (or key) of the offending item.")
    location: Vec2 | None = Field(default=None, description="Board-frame XY in metres.")
    layer: LayerId | None = Field(default=None, description="Layer id involved, if any.")
    path: str | None = Field(
        default=None,
        description="RFC 6901 JSON pointer into the board document locating the problem.",
        examples=["/tracks/12/layer"],
    )
    hint: str | None = Field(default=None, description="Suggested fix, phrased as an action.")
