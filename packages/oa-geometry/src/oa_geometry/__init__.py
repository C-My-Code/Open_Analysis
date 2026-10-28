# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Derived board geometry, ReferenceMap, cross-sections, regions (WP-104, interfaces.md section 2).

Entry point: ``BoardGeometry.build(board)``.
"""

from oa_geometry.copper import LayerIndex
from oa_geometry.errors import GeometryError, RegionError, TerminalError, UnknownItem
from oa_geometry.geometry import BoardGeometry
from oa_geometry.models import (
    BoundaryCrossing,
    DrillGeom,
    Island,
    NetTopology,
    ReferenceMap,
    ReferenceSpan,
    RefInfo,
    RegionCut,
    RegionSpec,
    ResolvedTerminal,
    ReturnVia,
    RoutedSegment,
    StitchCap,
    Stub,
    TopoEdge,
    TopoNode,
    ViaTransition,
    XsSample,
)
from oa_geometry.vias import via_stub, via_stubs
from oa_model.index import ItemRef

__all__ = [
    "BoardGeometry",
    "BoundaryCrossing",
    "DrillGeom",
    "GeometryError",
    "Island",
    "ItemRef",
    "LayerIndex",
    "NetTopology",
    "RefInfo",
    "ReferenceMap",
    "ReferenceSpan",
    "RegionCut",
    "RegionError",
    "RegionSpec",
    "ResolvedTerminal",
    "ReturnVia",
    "RoutedSegment",
    "StitchCap",
    "Stub",
    "TerminalError",
    "TopoEdge",
    "TopoNode",
    "UnknownItem",
    "ViaTransition",
    "XsSample",
    "via_stub",
    "via_stubs",
]
