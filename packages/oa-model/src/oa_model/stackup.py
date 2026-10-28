# SPDX-License-Identifier: GPL-3.0-or-later
"""Stackup layers (board-model.md section 6.2)."""

from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from oa_model._base import OAModel
from oa_model.common import Coord, LayerId, Length
from oa_model.materials import Material, NoRoughness, Roughness
from oa_model.units import parse_quantity


class Etch(OAModel):
    """Trapezoidal trace cross-section. Both forms are allowed; physics normalizes them."""

    kind: Literal["angle", "top_width_delta"] = Field(
        description="'angle': value is the etch angle; 'top_width_delta': value is w_bottom - w_top."
    )
    value: float = Field(
        description=(
            "kind='angle': radians from the base (pi/2 = vertical walls; '60deg' parses). "
            "kind='top_width_delta': metres, (w_bottom - w_top)."
        )
    )
    reference: Literal["bottom", "top"] = Field(
        default="bottom", description="Which width the drawn track width denotes."
    )

    @model_validator(mode="before")
    @classmethod
    def _parse_value(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("value"), str):
            dim = "angle" if data.get("kind") == "angle" else "length"
            data = {**data, "value": parse_quantity(data["value"], dim)}
        return data


class ConductorLayer(OAModel):
    kind: Literal["conductor"] = Field(
        default="conductor", description="Discriminator tag; always 'conductor'."
    )
    id: LayerId = Field(description="Layer id 'L1'..'Ln' (top to bottom).")
    index: int = Field(ge=1, description="1-based copper index; 1 = top copper.")
    source_name: str | None = Field(
        default=None, description="Name in the source: 'F.Cu', 'In1.Cu'."
    )
    user_name: str | None = Field(default=None, description="User alias from the source.")
    role: Literal["signal", "plane", "mixed", "jumper", "unknown"] = Field(
        default="signal", description="Intended use of the layer (extensible)."
    )
    thickness: Length = Field(description="Finished copper thickness, metres.")
    z_bottom: Coord = Field(description="Bottom surface z in metres (z=0 is the stack bottom).")
    z_top: Coord = Field(description="Top surface z in metres.")
    material: str = Field(
        default="copper", description="Name of a ConductorMaterial in Stackup.materials."
    )
    roughness_top: Roughness = Field(
        default=NoRoughness(), description="Roughness of the surface facing +z."
    )
    roughness_bottom: Roughness = Field(
        default=NoRoughness(), description="Roughness of the surface facing -z."
    )
    roughness_side: Roughness = Field(default=NoRoughness(), description="Sidewall roughness.")
    etch: Etch | None = Field(default=None, description="Trapezoid etch; None = rectangular.")
    plating_thickness: Length | None = Field(
        default=None, description="Extra plating on outer layers, metres, if separately known."
    )


class DielectricLayer(OAModel):
    kind: Literal["dielectric"] = Field(
        default="dielectric", description="Discriminator tag; always 'dielectric'."
    )
    id: LayerId = Field(description="Layer id 'D1'..'Dm' (top to bottom).")
    index: int = Field(ge=1, description="1-based dielectric index; 1 = topmost slab.")
    source_name: str | None = Field(default=None, description="Source name; sub-plies end '/2'.")
    construction: Literal["core", "prepreg", "unknown"] = Field(
        default="unknown", description="Laminate construction."
    )
    thickness: Length = Field(gt=0.0, description="Thickness, metres, > 0.")
    z_bottom: Coord = Field(description="Bottom surface z, metres.")
    z_top: Coord = Field(description="Top surface z, metres.")
    material: str = Field(description="Name of a DielectricMaterial in Stackup.materials.")
    fills_between_traces: bool = Field(
        default=True, description="Prepreg flows into copper gaps of the adjacent layers."
    )


class MaskLayer(OAModel):
    kind: Literal["mask"] = Field(default="mask", description="Discriminator tag; always 'mask'.")
    id: Literal["MT", "MB"] = Field(description="'MT' top solder mask, 'MB' bottom.")
    source_name: str | None = Field(default=None, description="Source layer name.")
    thickness: Length = Field(description="Mask thickness over bare laminate, metres.")
    thickness_over_copper: Length | None = Field(
        default=None, description="Mask thickness over copper, metres; None = same as thickness."
    )
    z_bottom: Coord = Field(description="Bottom surface z, metres.")
    z_top: Coord = Field(description="Top surface z, metres.")
    material: str = Field(description="Name of a DielectricMaterial in Stackup.materials.")
    present: bool = Field(default=True, description="False if the mask is absent.")


StackLayer = Annotated[ConductorLayer | DielectricLayer | MaskLayer, Field(discriminator="kind")]


class Stackup(OAModel):
    layers: tuple[StackLayer, ...] = Field(
        description="Physical order, top (highest z) first. Layers are referenced by id."
    )
    materials: tuple[Material, ...] = Field(description="Materials, unique by name.")
    finish: str | None = Field(default=None, description="Surface finish: 'ENIG', 'HASL', ...")
    total_thickness: Length = Field(description="Overall stack height, metres (= layers[0].z_top).")
    known: bool = Field(
        default=True, description="False when the source had no stackup and defaults were used."
    )
