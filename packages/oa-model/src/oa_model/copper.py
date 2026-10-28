# SPDX-License-Identifier: GPL-3.0-or-later
"""Tracks, zones and copper shapes (board-model.md section 9)."""

from typing import Literal

from pydantic import Field

from oa_model._base import OAModel
from oa_model.common import ItemId, LayerId, Length, NetId, SourceRef
from oa_model.geometry import PathSeg, Polygon


class Track(OAModel):
    """One routed conductor with constant width along a centreline path."""

    id: ItemId
    net: NetId | None = Field(description="Net name, or None.")
    layer: LayerId = Field(description="Conductor layer id.")
    width: Length = Field(gt=0.0, description="Track width, metres.")
    path: tuple[PathSeg, ...] = Field(
        min_length=1, description="Contiguous segments: seg[i].end == seg[i+1].start."
    )
    end_caps: Literal["round", "square", "flat"] = Field(
        default="round", description="End cap style (KiCad: round)."
    )
    source: SourceRef | None = Field(default=None, description="Originating item.")


class ZoneFill(OAModel):
    layer: LayerId = Field(description="Conductor layer of this fill.")
    polygons: tuple[Polygon, ...] = Field(
        description="ACTUAL copper (thermals and clearances already cut)."
    )


class KeepoutRules(OAModel):
    tracks: bool = Field(default=False, description="Forbid tracks.")
    vias: bool = Field(default=False, description="Forbid vias.")
    pads: bool = Field(default=False, description="Forbid pads.")
    copper_pour: bool = Field(default=False, description="Forbid copper pours.")
    footprints: bool = Field(default=False, description="Forbid footprints.")


class Zone(OAModel):
    id: ItemId
    net: NetId | None = Field(description="Net name; None for keepouts and unconnected pours.")
    layers: tuple[LayerId, ...] = Field(description="Conductor layer ids.")
    priority: int = Field(default=0, description="Fill priority; higher wins.")
    outline: Polygon = Field(description="User-drawn boundary (not copper).")
    fills: tuple[ZoneFill, ...] = Field(default=(), description="Filled copper per layer.")
    fill_state: Literal["filled", "unfilled", "stale_suspected", "refilled_by_tool"] = Field(
        default="filled", description="Fill freshness. PI analyses refuse 'unfilled'."
    )
    hatch: bool = Field(default=False, description="Hatched fill (fills still hold real copper).")
    is_teardrop: bool = Field(default=False, description="Teardrop zone.")
    keepout: KeepoutRules | None = Field(default=None, description="Not None => rule area.")
    min_thickness: Length | None = Field(default=None, description="Minimum fill width, metres.")
    source: SourceRef | None = Field(default=None, description="Originating item.")


class CopperShape(OAModel):
    """Graphic copper that is neither track, pad nor zone, already converted to polygons."""

    id: ItemId
    net: NetId | None = Field(description="Net name, or None.")
    layer: LayerId = Field(description="Conductor layer id.")
    polygon: Polygon = Field(description="Copper outline.")
    origin: Literal["graphic", "text", "other"] = Field(
        default="graphic", description="What produced the shape (extensible)."
    )
    source: SourceRef | None = Field(default=None, description="Originating item.")
