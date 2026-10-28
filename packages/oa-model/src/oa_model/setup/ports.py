# SPDX-License-Identifier: GPL-3.0-or-later
"""Port definitions referencing board items by stable keys (board-model.md section 12.2)."""

from typing import Annotated, Literal

from pydantic import Field

from oa_model._base import OAModel
from oa_model.common import ItemId, LayerId, Length, NetId, Resistance, Vec2


class PadTerminal(OAModel):
    """Either a pad id, or a refdes plus pin number."""

    kind: Literal["pad"] = Field(default="pad", description="Discriminator tag; always 'pad'.")
    pad: ItemId | None = Field(default=None, description="Pad id.")
    component: str | None = Field(default=None, description="Refdes, with `pin`.")
    pin: str | None = Field(default=None, description="Pin number on the component.")


class PinGroupTerminal(OAModel):
    """All pads of a component on the given net(s), shorted into one terminal."""

    kind: Literal["pin_group"] = Field(
        default="pin_group", description="Discriminator tag; always 'pin_group'."
    )
    component: str = Field(description="Refdes.")
    nets: tuple[NetId, ...] = Field(description="Net names whose pads are grouped.")
    pins: tuple[str, ...] = Field(default=(), description="Pin numbers; empty = all on the nets.")


class PointTerminal(OAModel):
    kind: Literal["point"] = Field(
        default="point", description="Discriminator tag; always 'point'."
    )
    position: Vec2 = Field(description="Board-frame (x, y), metres.")
    layer: LayerId = Field(description="Conductor layer id.")
    net: NetId | None = Field(
        default=None, description="Optional assertion; resolution fails if copper there differs."
    )


class NearestReferenceTerminal(OAModel):
    """Nearest reference-plane copper under or around the positive terminal."""

    kind: Literal["nearest_reference"] = Field(
        default="nearest_reference", description="Discriminator tag; always 'nearest_reference'."
    )
    nets: tuple[NetId, ...] = Field(default=(), description="Restrict to nets; empty = any ground.")
    search_radius: Length = Field(default=2e-3, description="Search radius, metres.")


Terminal = Annotated[
    PadTerminal | PinGroupTerminal | PointTerminal | NearestReferenceTerminal,
    Field(discriminator="kind"),
]


class LumpedPort(OAModel):
    kind: Literal["lumped"] = Field(
        default="lumped", description="Discriminator tag; always 'lumped'."
    )
    name: str = Field(description="Port name, unique in the setup.")
    positive: Terminal = Field(description="Positive terminal.")
    negative: Terminal = Field(description="Negative terminal.")
    z0: Resistance = Field(default=50.0, description="Reference impedance, ohms.")


class TraceEndPort(OAModel):
    """Wave or TL port where a trace crosses the extraction-region boundary."""

    kind: Literal["trace_end"] = Field(
        default="trace_end", description="Discriminator tag; always 'trace_end'."
    )
    name: str = Field(description="Port name, unique in the setup.")
    nets: tuple[NetId, ...] = Field(
        description="1 net (single-ended) or 2 nets (differential, P first)."
    )
    side: Literal["xmin", "xmax", "ymin", "ymax", "auto"] = Field(
        default="auto", description="Region face to place the port on."
    )
    deembed: Length = Field(default=0.0, description="Reference-plane shift, metres.")
    z0: Resistance | None = Field(
        default=None, description="Ohms; None = renormalize to the computed line impedance."
    )
    modes: Literal["single", "differential", "both"] = Field(
        default="single", description="Modes to excite."
    )


class CoaxPort(OAModel):
    kind: Literal["coax"] = Field(default="coax", description="Discriminator tag; always 'coax'.")
    name: str = Field(description="Port name, unique in the setup.")
    pad: PadTerminal = Field(description="Connector centre pin pad.")
    outer_radius: Length = Field(description="Outer conductor radius, metres.")
    inner_radius: Length | None = Field(
        default=None, description="Inner radius, metres; None = drill radius."
    )
    dielectric: str = Field(default="PTFE", description="Material name between conductors.")
    z0: Resistance = Field(default=50.0, description="Reference impedance, ohms.")


class PlanePairPort(OAModel):
    """PI/EMI port between two planes at a location."""

    kind: Literal["plane_pair"] = Field(
        default="plane_pair", description="Discriminator tag; always 'plane_pair'."
    )
    name: str = Field(description="Port name, unique in the setup.")
    position: Vec2 = Field(description="Board-frame (x, y), metres.")
    upper_layer: LayerId = Field(description="Upper plane layer id.")
    lower_layer: LayerId = Field(description="Lower plane layer id.")
    radius: Length = Field(default=0.15e-3, description="Port radius, metres.")


Port = Annotated[LumpedPort | TraceEndPort | CoaxPort | PlanePairPort, Field(discriminator="kind")]
