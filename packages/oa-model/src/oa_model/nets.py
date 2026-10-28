# SPDX-License-Identifier: GPL-3.0-or-later
"""Nets, net classes and differential pairs (board-model.md section 7)."""

from typing import Literal

from pydantic import Field

from oa_model._base import OAModel
from oa_model.common import Fraction, ItemId, Length, NetId, Resistance, SourceRef


class Net(OAModel):
    id: NetId
    kind: Literal["signal", "power", "ground", "unknown"] = Field(
        default="unknown", description="Reader heuristic; setup intent overrides it."
    )
    net_class: str | None = Field(
        default=None, description="Name of the NetClass this net belongs to."
    )
    source: SourceRef | None = Field(default=None, description="Originating item.")


class NetClass(OAModel):
    name: str = Field(description="Net class name (its key).", examples=["USB_90ohm"])
    track_width: Length | None = Field(default=None, description="Default track width, metres.")
    clearance: Length | None = Field(default=None, description="Clearance, metres.")
    via_diameter: Length | None = Field(default=None, description="Via pad diameter, metres.")
    via_drill: Length | None = Field(default=None, description="Via drill diameter, metres.")
    dp_width: Length | None = Field(default=None, description="Diff-pair track width, metres.")
    dp_gap: Length | None = Field(default=None, description="Diff-pair gap, metres.")
    target_z_se: Resistance | None = Field(
        default=None, gt=0, description="Target single-ended impedance, ohms."
    )
    target_z_diff: Resistance | None = Field(
        default=None, gt=0, description="Target differential impedance, ohms."
    )
    target_tolerance: Fraction | None = Field(
        default=None, gt=0, lt=1, description="Impedance tolerance as a fraction (0.1 = 10 %)."
    )
    tuning_profile: str | None = Field(default=None, description="KiCad tuning profile name.")
    patterns: tuple[str, ...] = Field(
        default=(), description="Glob patterns assigning nets (informational)."
    )
    source: SourceRef | None = Field(default=None, description="Originating item.")


class DiffPair(OAModel):
    id: ItemId = Field(description="Pair id, e.g. 'dp:USB_D'.", examples=["dp:USB_D"])
    p_net: NetId = Field(description="Name of the positive net.")
    n_net: NetId = Field(description="Name of the negative net.")
    origin: Literal["naming", "netclass", "source_explicit", "user"] = Field(
        description="How the pair was determined (extensible)."
    )
    target_z_diff: Resistance | None = Field(
        default=None, gt=0, description="Target differential impedance, ohms."
    )
