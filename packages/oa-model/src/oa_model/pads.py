# SPDX-License-Identifier: GPL-3.0-or-later
"""Pads (board-model.md section 8)."""

from typing import Literal

from pydantic import Field

from oa_model._base import OAModel
from oa_model.common import Angle, ItemId, Length, NetId, SourceRef, Vec2


class Pad(OAModel):
    id: ItemId
    component: str | None = Field(
        description="Refdes of the owning component (its key); None for free pads."
    )
    number: str = Field(description="Pin number '1', 'A12'; '' for mechanical pads.")
    padstack: ItemId = Field(description="Id of the Padstack.")
    position: Vec2 = Field(description="Board-frame (x, y), metres.")
    rotation: Angle = Field(description="Absolute rotation, radians, counter-clockwise.")
    net: NetId | None = Field(description="Net name, or None if unconnected.")
    kind: Literal["smd", "through_hole", "npth", "connector_edge", "aperture"] = Field(
        description="Pad type."
    )
    pin_function: str | None = Field(default=None, description="Pin function name.")
    pin_type: str | None = Field(default=None, description="Source pin type, e.g. 'power_in'.")
    die_length: Length | None = Field(default=None, description="Package length, metres.")
    die_delay: float | None = Field(default=None, ge=0, description="Package delay, seconds.")
    source: SourceRef | None = Field(default=None, description="Originating item.")
