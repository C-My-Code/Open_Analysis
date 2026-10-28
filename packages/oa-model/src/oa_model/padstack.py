# SPDX-License-Identifier: GPL-3.0-or-later
"""Pad shapes, drills and padstacks (board-model.md section 8). Shapes are in the pad-local frame."""

from typing import Annotated, Literal

from pydantic import Field

from oa_model._base import OAModel
from oa_model.common import Coord, ItemId, LayerId, Length, Vec2
from oa_model.geometry import Polygon


class CircleShape(OAModel):
    kind: Literal["circle"] = Field(
        default="circle", description="Discriminator tag; always 'circle'."
    )
    diameter: Length = Field(description="Diameter, metres.")


class RectShape(OAModel):
    kind: Literal["rect"] = Field(default="rect", description="Discriminator tag; always 'rect'.")
    width: Length = Field(description="Size along local x, metres.")
    height: Length = Field(description="Size along local y, metres.")


class RoundRectShape(OAModel):
    kind: Literal["roundrect"] = Field(
        default="roundrect", description="Discriminator tag; always 'roundrect'."
    )
    width: Length = Field(description="Size along local x, metres.")
    height: Length = Field(description="Size along local y, metres.")
    corner_radius: Length = Field(description="Corner radius, metres.")


class ObroundShape(OAModel):
    """KiCad 'oval'."""

    kind: Literal["obround"] = Field(
        default="obround", description="Discriminator tag; always 'obround'."
    )
    width: Length = Field(description="Size along local x, metres.")
    height: Length = Field(description="Size along local y, metres.")


class ChamferedRectShape(OAModel):
    kind: Literal["chamfered_rect"] = Field(
        default="chamfered_rect", description="Discriminator tag; always 'chamfered_rect'."
    )
    width: Length = Field(description="Size along local x, metres.")
    height: Length = Field(description="Size along local y, metres.")
    chamfer: Length = Field(description="Chamfer size, metres.")
    corners: tuple[Literal["tl", "tr", "bl", "br"], ...] = Field(
        description="Chamfered corners (top-left, ...)."
    )
    corner_radius: Length = Field(default=0.0, description="Radius of the other corners, metres.")


class TrapezoidShape(OAModel):
    kind: Literal["trapezoid"] = Field(
        default="trapezoid", description="Discriminator tag; always 'trapezoid'."
    )
    width: Length = Field(description="Size along local x, metres.")
    height: Length = Field(description="Size along local y, metres.")
    delta_x: Coord = Field(default=0.0, description="KiCad rect_delta x, metres.")
    delta_y: Coord = Field(default=0.0, description="KiCad rect_delta y, metres.")


class PolygonShape(OAModel):
    """Custom pads: anchor union primitives, already unioned by the reader."""

    kind: Literal["polygon"] = Field(
        default="polygon", description="Discriminator tag; always 'polygon'."
    )
    polygons: tuple[Polygon, ...] = Field(description="Pad-local polygons.")


PadShape = Annotated[
    CircleShape
    | RectShape
    | RoundRectShape
    | ObroundShape
    | ChamferedRectShape
    | TrapezoidShape
    | PolygonShape,
    Field(discriminator="kind"),
]


class Drill(OAModel):
    kind: Literal["plated", "npth"] = Field(description="Plated hole or non-plated hole.")
    shape: Literal["round", "slot"] = Field(default="round", description="Hole shape.")
    diameter: Length = Field(description="Round: diameter. Slot: width. Metres.")
    slot_length: Length | None = Field(
        default=None, description="Slot overall length along local x, metres."
    )
    plating_thickness: Length = Field(
        default=25e-6, description="Barrel plating thickness, metres (IPC class 2 ~ 25 um)."
    )
    offset: Vec2 = Field(
        default=(0.0, 0.0),
        description="Drill centre relative to the pad origin, metres, pad-local.",
    )


class PadstackLayer(OAModel):
    layer: LayerId = Field(description="Conductor layer id this entry applies to.")
    pad: PadShape | None = Field(description="Copper shape; None = no copper on this layer.")
    antipad: PadShape | None = Field(
        default=None, description="Clearance cut into plane copper, if the source states it."
    )
    thermal_spoke_width: Length | None = Field(default=None, description="Spoke width, metres.")
    thermal_gap: Length | None = Field(default=None, description="Thermal gap, metres.")
    connection: Literal["solid", "thermal", "none", "unknown"] = Field(
        default="unknown", description="Connection style to same-net zones."
    )


class Padstack(OAModel):
    id: ItemId = Field(description="Padstack id shared by identical pads/vias.")
    drill: Drill | None = Field(description="Drill; None for SMD pads.")
    layers: tuple[PadstackLayer, ...] = Field(
        description="Fully resolved: one entry per conductor layer the pad or via touches."
    )
    remove_unused_layers: bool = Field(default=False, description="Remove unconnected inner pads.")
    keep_end_layers: bool = Field(default=True, description="Keep end-layer pads when removing.")
    mask_opening_top: PadShape | None = Field(
        default=None, description="Top solder-mask opening, pad-local."
    )
    mask_opening_bottom: PadShape | None = Field(
        default=None, description="Bottom solder-mask opening, pad-local."
    )
