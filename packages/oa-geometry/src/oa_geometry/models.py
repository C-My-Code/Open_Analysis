# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="import-untyped,unused-ignore"
"""Public data models of the geometry service (interfaces.md section 2).

Lengths are metres, angles radians, board frame (x right, y up). The spec names several types
without defining them (``TopoNode``, ``TopoEdge``, ``Stub``, ``Island``, ``ViaTransition``,
``BoundaryCrossing``, ``ResolvedTerminal``, ``DrillGeom``, ``LayerIndex``); their fields here are
the WP-104 definitions, recorded in ``docs/change-requests/WP-104-undefined-types.md``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field, PrivateAttr

from oa_model._base import OAModel
from oa_model.common import Frequency, ItemId, LayerId, NetId, Vec2
from oa_model.em import EMTerminalGeom, Structure3D
from oa_model.geometry import PathSeg
from oa_model.index import ItemRef
from oa_model.xsection import CrossSection

if TYPE_CHECKING:
    import networkx

__all__ = [
    "BoundaryCrossing",
    "DrillGeom",
    "Island",
    "NetTopology",
    "RefInfo",
    "ReferenceMap",
    "ReferenceSpan",
    "RegionCut",
    "RegionSpec",
    "ResolvedTerminal",
    "ReturnVia",
    "RoutedSegment",
    "StitchCap",
    "Stub",
    "TopoEdge",
    "TopoNode",
    "ViaTransition",
    "XsSample",
]

BBox = tuple[float, float, float, float]


class RoutedSegment(OAModel):
    """One line or arc of a routed net (a whole track segment or a piece of it)."""

    id: str = Field(
        description="'<track_id>' for a whole single-segment track, '<track_id>#k' for the k-th "
        "segment of a multi-segment track, '<id>.j' for pieces split at T-junctions."
    )
    net: NetId = Field(description="Net name.")
    layer: LayerId = Field(description="Conductor layer id.")
    width: float = Field(gt=0, description="Drawn track width [m].")
    seg: PathSeg = Field(description="The line or arc (board frame, metres).")
    length: float = Field(ge=0, description="Path length of the segment [m].")
    s_start: float = Field(
        default=0.0,
        ge=0,
        description="Cumulative path length from the topology root to the segment start [m]. "
        "0 when the segment was not obtained through a net topology.",
    )


class TopoNode(OAModel):
    id: str = Field(
        description="Unique node id: 'pad:<id>', 'via:<id>@<layer>', 'zone:<id>@<layer>', "
        "'j<k>', 'v<k>', 'e<k>'."
    )
    kind: Literal["pad", "via", "junction", "vertex", "end", "zone"] = Field(
        description="Pad, via contact on one layer, T-junction (degree >= 3), plain joint of two "
        "segments, free track end, or zone-copper hub."
    )
    position: Vec2 = Field(description="Board-frame (x, y) [m].")
    layer: LayerId | None = Field(description="Conductor layer id of the contact, if any.")
    ref: ItemId | None = Field(default=None, description="Pad or via id for pad/via nodes.")


class TopoEdge(OAModel):
    id: str = Field(description="Edge id: the RoutedSegment id, 'barrel:<via>:<k>', 'zone:<k>'.")
    kind: Literal["segment", "via", "zone"] = Field(
        description="Routed segment, via barrel, or zone-copper connection."
    )
    a: str = Field(description="Id of the first node.")
    b: str = Field(description="Id of the second node.")
    length: float = Field(ge=0, description="Path length [m]; barrels use their z extent.")
    layer: LayerId | None = Field(default=None, description="Layer of a segment/zone edge.")
    seg_id: str | None = Field(default=None, description="RoutedSegment id for segment edges.")
    s_start: float = Field(default=0.0, ge=0, description="Distance from the root to node a [m].")


class Stub(OAModel):
    """A dead-end branch: the path from a junction to a free end, or a pad/via remnant."""

    kind: Literal["branch", "pad_stub", "via_stub"] = Field(
        description="'branch': track branch ending without a pad; 'pad_stub': pad reached only "
        "by a dead-end branch; 'via_stub': via barrel past its last used layer."
    )
    end_node: str = Field(description="Node id at the free end.")
    from_node: str = Field(description="Node id where the stub leaves the main path.")
    length: float = Field(ge=0, description="Stub length [m].")
    edges: tuple[str, ...] = Field(description="Edge ids along the stub, from the junction.")


class NetTopology(OAModel):
    """Graph of a net; trees are rooted at the root pad (see ``BoardGeometry.topology``)."""

    net: NetId = Field(description="Net name.")
    root: str | None = Field(description="Id of the root node, or None for an empty net.")
    nodes: tuple[TopoNode, ...] = Field(description="Pads, via contacts, junctions, free ends.")
    edges: tuple[TopoEdge, ...] = Field(description="Segments, via barrels, zone connections.")
    stubs: tuple[Stub, ...] = Field(description="Branches ending without a pad, and pad stubs.")

    def networkx(self) -> networkx.MultiGraph:
        """The topology as a networkx MultiGraph (node attrs: kind, layer, position)."""
        import networkx as nx

        g: nx.MultiGraph = nx.MultiGraph()
        for n in self.nodes:
            g.add_node(n.id, kind=n.kind, layer=n.layer, position=n.position, ref=n.ref)
        for e in self.edges:
            g.add_edge(
                e.a, e.b, key=e.id, kind=e.kind, length=e.length, layer=e.layer, seg_id=e.seg_id
            )
        return g


class Island(OAModel):
    """A group of copper of one net that is galvanically connected (through pads, tracks, zones,
    vias and plated through-hole pads)."""

    id: str = Field(description="'<net>#<k>', k counted from 0, largest area first.")
    net: NetId = Field(description="Net name.")
    layers: tuple[LayerId, ...] = Field(description="Conductor layers the island has copper on.")
    items: tuple[ItemRef, ...] = Field(description="Board items (kind, id) belonging to it.")
    area: float = Field(ge=0, description="Total copper area over all layers [m^2].")
    bbox: BBox = Field(description="xmin, ymin, xmax, ymax of the copper [m].")


class DrillGeom(OAModel):
    """A drilled hole (via barrel, through-hole pad, NPTH, slot)."""

    id: str = Field(description="'<owner kind>:<owner id>'.")
    owner: ItemRef = Field(description="The via or pad that owns the hole.")
    position: Vec2 = Field(description="Hole centre, board frame [m].")
    diameter: float = Field(gt=0, description="Round: diameter; slot: width [m].")
    slot_length: float | None = Field(default=None, description="Slot overall length [m].")
    rotation: float = Field(default=0.0, description="Slot orientation, radians CCW.")
    kind: Literal["plated", "npth"] = Field(description="Plated or non-plated.")
    net: NetId | None = Field(description="Net of the owner.")
    start_layer: LayerId = Field(description="Upper layer of the hole's span.")
    end_layer: LayerId = Field(description="Lower layer of the hole's span.")


class RefInfo(OAModel):
    layer: LayerId = Field(description="Conductor layer id of the reference copper.")
    net: NetId = Field(description="Net of the reference copper.")
    distance: float = Field(
        ge=0, description="Vertical distance trace to the plane surface facing it [m]."
    )
    coverage: float = Field(
        ge=0,
        le=1,
        description="Fraction (0..1) of the footprint (W + 2*k*h wide, centred on the trace) "
        "covered by the reference copper.",
    )
    edge_distance: float = Field(
        ge=0,
        description="Lateral distance from the trace body to the nearest plane edge [m]; 0 when "
        "the trace crosses the edge. Capped at 10*distance.",
    )


class ReferenceSpan(OAModel):
    s_start: float = Field(ge=0, description="Span start along the segment [m].")
    s_end: float = Field(ge=0, description="Span end along the segment [m].")
    above: RefInfo | None = Field(description="Reference above (higher z), None if none.")
    below: RefInfo | None = Field(description="Reference below (lower z), None if none.")


class ReturnVia(OAModel):
    via: ItemId = Field(description="Id of the nearby return (reference-net) via.")
    net: NetId = Field(description="Net of the return via.")
    distance: float = Field(ge=0, description="Distance to the signal via [m].")


class StitchCap(OAModel):
    refdes: str = Field(description="Capacitor reference designator.")
    distance: float = Field(ge=0, description="Distance to the signal via [m].")
    nets: tuple[NetId, NetId] = Field(description="The two nets the capacitor connects.")


class ViaTransition(OAModel):
    via: ItemId = Field(description="Signal via id.")
    net: NetId = Field(description="Signal net.")
    position: Vec2 = Field(description="Via centre, board frame [m].")
    from_layer: LayerId = Field(description="Upper layer where routing attaches.")
    to_layer: LayerId = Field(description="Lower layer where routing attaches.")
    from_ref: RefInfo | None = Field(description="Reference of the routing at the upper layer.")
    to_ref: RefInfo | None = Field(description="Reference of the routing at the lower layer.")
    ref_net_changes: bool = Field(description="Reference net differs between the two ends.")
    return_vias: tuple[ReturnVia, ...] = Field(
        description="Nearest vias of the reference nets, closest first."
    )
    stitching_caps: tuple[StitchCap, ...] = Field(
        description="Nearby capacitors tying the two reference nets, closest first."
    )


class ReferenceMap(OAModel):
    k_buffer: float = Field(
        default=3.0, gt=0, description="Footprint half-width = W/2 + k*h (dimensionless k)."
    )
    spans: tuple[tuple[str, tuple[ReferenceSpan, ...]], ...] = Field(
        description="RoutedSegment.id -> spans along it."
    )
    via_transitions: tuple[ViaTransition, ...] = Field(
        description="Signal vias: from/to references, return vias and caps near."
    )
    _index: dict[str, tuple[ReferenceSpan, ...]] | None = PrivateAttr(default=None)

    def for_segment(self, seg_id: str) -> tuple[ReferenceSpan, ...]:
        """Spans of one segment (empty tuple when unknown)."""
        if self._index is None:
            self._index = dict(self.spans)
        return self._index.get(seg_id, ())


class XsSample(OAModel):
    s: float = Field(ge=0, description="Path length from the topology root [m].")
    position: Vec2 = Field(description="Sample point, board frame [m].")
    layer: LayerId = Field(description="Conductor layer of the segment.")
    seg_id: str = Field(description="RoutedSegment id.")
    xs: CrossSection = Field(description="Canonical cross-section at the sample.")
    event: Literal[
        "regular",
        "width_change",
        "ref_change",
        "layer_change",
        "neckdown",
        "plane_edge",
        "no_reference",
    ] = Field(default="regular", description="What makes this sample special, if anything.")


class RegionSpec(OAModel):
    nets: tuple[NetId, ...] = Field(description="Signal nets of interest.")
    reference_nets: tuple[NetId, ...] = Field(
        default=(), description="Reference nets; empty = automatic from the ReferenceMap."
    )
    bbox: BBox | None = Field(
        default=None, description="xmin, ymin, xmax, ymax [m]; None = automatic."
    )
    margin: float | None = Field(
        default=None,
        ge=0,
        description="Margin around the automatic bbox [m]; None = max(5*h_max, 3*w_max, "
        "lambda_min/10).",
    )
    keep_all_copper: bool = Field(
        default=True, description="Keep every copper polygon in the bbox, not only the nets."
    )
    layers: tuple[LayerId, ...] | None = Field(
        default=None, description="Restrict the z range to these layers; None = full stack."
    )
    f_max: Frequency | None = Field(default=None, description="Highest frequency of interest [Hz].")


class BoundaryCrossing(OAModel):
    net: NetId = Field(description="Net of the crossing trace.")
    layer: LayerId = Field(description="Conductor layer.")
    seg_id: str = Field(description="RoutedSegment id.")
    face: Literal["xmin", "xmax", "ymin", "ymax"] = Field(description="Region face crossed.")
    position: Vec2 = Field(description="Crossing point on the face, board frame [m].")
    direction: float = Field(description="Travel direction at the crossing, radians CCW from +x.")
    width: float = Field(gt=0, description="Track width [m].")
    z_bottom: float = Field(description="Bottom of the trace layer [m].")
    z_top: float = Field(description="Top of the trace layer [m].")


class RegionCut(OAModel):
    bbox: BBox = Field(description="xmin, ymin, xmax, ymax of the cut [m].")
    structure: Structure3D = Field(description="Geometry and materials for EM/QS/thermal.")
    boundary_crossings: tuple[BoundaryCrossing, ...] = Field(
        description="Traces crossing the box: candidate ports."
    )


class ResolvedTerminal(OAModel):
    kind: Literal["pad", "pin_group", "point", "nearest_reference"] = Field(
        description="Kind of the terminal that was resolved."
    )
    position: Vec2 = Field(description="Contact centre, board frame [m].")
    z: float = Field(description="Contact height [m].")
    layer: LayerId | None = Field(description="Conductor layer of the contact.")
    net: NetId | None = Field(description="Net of the copper under the contact.")
    pads: tuple[ItemId, ...] = Field(description="Pads making up the terminal (if any).")
    extent: tuple[float, float] = Field(description="Contact patch size along x, y [m].")

    def to_em(self) -> EMTerminalGeom:
        """The solver-neutral geometry form (``oa_model.em.EMTerminalGeom``)."""
        return EMTerminalGeom(
            position=self.position, z=self.z, layer=self.layer, extent=self.extent
        )
