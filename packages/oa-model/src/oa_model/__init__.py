# SPDX-License-Identifier: GPL-3.0-or-later
"""Open_Analysis neutral board model (board-model.md, frozen v1)."""

from oa_model._base import GEOM_EPS, OAModel
from oa_model.arrays import Floats, NDArray, Points
from oa_model.board import Board, BoardMeta, BoardOutline, Component
from oa_model.common import Diagnostic, SourceRef
from oa_model.copper import CopperShape, KeepoutRules, Track, Zone, ZoneFill
from oa_model.em import EMProblem, Structure3D
from oa_model.errors import (
    BoardValidationError,
    OAError,
    SetupError,
    UnsupportedSchemaVersion,
)
from oa_model.geometry import ArcSeg, LineSeg, Polygon
from oa_model.index import BoardIndex, ItemRef
from oa_model.io import dump_board, dumps_board, load_board, loads_board
from oa_model.materials import (
    ConductorMaterial,
    ConstantDkDf,
    DielectricMaterial,
    DjordjevicSarkar,
)
from oa_model.migrations import SCHEMA_VERSION
from oa_model.nets import DiffPair, Net, NetClass
from oa_model.pads import Pad
from oa_model.padstack import Drill, Padstack, PadstackLayer
from oa_model.physics import PhysicsOverlay, apply_physics, load_physics
from oa_model.setup import (
    AnalysisSetup,
    DesignIntent,
    ProjectManifest,
    ResolvedIntent,
    resolve_intent,
)
from oa_model.stackup import ConductorLayer, DielectricLayer, MaskLayer, Stackup
from oa_model.units import parse_quantity
from oa_model.validate import validate_board
from oa_model.vias import Backdrill, Via, ViaProtection
from oa_model.xsection import CrossSection

__all__ = [
    "GEOM_EPS",
    "SCHEMA_VERSION",
    "AnalysisSetup",
    "ArcSeg",
    "Backdrill",
    "Board",
    "BoardIndex",
    "BoardMeta",
    "BoardOutline",
    "BoardValidationError",
    "Component",
    "ConductorLayer",
    "ConductorMaterial",
    "ConstantDkDf",
    "CopperShape",
    "CrossSection",
    "DesignIntent",
    "Diagnostic",
    "DielectricLayer",
    "DielectricMaterial",
    "DiffPair",
    "DjordjevicSarkar",
    "Drill",
    "EMProblem",
    "Floats",
    "ItemRef",
    "KeepoutRules",
    "LineSeg",
    "MaskLayer",
    "NDArray",
    "Net",
    "NetClass",
    "OAError",
    "OAModel",
    "Pad",
    "Padstack",
    "PadstackLayer",
    "PhysicsOverlay",
    "Points",
    "Polygon",
    "ProjectManifest",
    "ResolvedIntent",
    "SetupError",
    "SourceRef",
    "Stackup",
    "Structure3D",
    "Track",
    "UnsupportedSchemaVersion",
    "Via",
    "ViaProtection",
    "Zone",
    "ZoneFill",
    "apply_physics",
    "dump_board",
    "dumps_board",
    "load_board",
    "load_physics",
    "loads_board",
    "parse_quantity",
    "resolve_intent",
    "validate_board",
]
