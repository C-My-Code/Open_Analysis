# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Result containers and violation types (interfaces.md sections 1.1, 3.7, 4.1)."""

from oa_results.artifact import ArtifactRef
from oa_results.bundle import ArtifactOnly, Provenance, ResultBundle, ResultData, ResultItem
from oa_results.container import (
    AccuracyClass,
    PayloadBase,
    ResultContainer,
    SolverProvenance,
    ValidityFlag,
)
from oa_results.extraction import ExtractionResult, FarFieldResult, MatrixPassivity
from oa_results.fields import (
    FieldData,
    FieldPiece,
    FieldResult,
    RasterGrid2D,
    TetMesh3D,
    TriMesh2D,
)
from oa_results.network import NetworkResult, PortMapEntry
from oa_results.profile import ProfileEvent, ProfileResult
from oa_results.quality import QualityCriteria, QualityReport
from oa_results.rlgc import RLGC, ImpedanceResult, LineParams, RLGCResultSet
from oa_results.scalars import Scalar, ScalarSet
from oa_results.tables import ColumnSpec, TableResult
from oa_results.violations import (
    Remediation,
    RemediationAction,
    Severity,
    Violation,
    ViolationSet,
    Waiver,
)

__all__ = [
    "RLGC",
    "AccuracyClass",
    "ArtifactOnly",
    "ArtifactRef",
    "ColumnSpec",
    "ExtractionResult",
    "FarFieldResult",
    "FieldData",
    "FieldPiece",
    "FieldResult",
    "ImpedanceResult",
    "LineParams",
    "MatrixPassivity",
    "NetworkResult",
    "PayloadBase",
    "PortMapEntry",
    "ProfileEvent",
    "ProfileResult",
    "Provenance",
    "QualityCriteria",
    "QualityReport",
    "RLGCResultSet",
    "RasterGrid2D",
    "Remediation",
    "RemediationAction",
    "ResultBundle",
    "ResultContainer",
    "ResultData",
    "ResultItem",
    "Scalar",
    "ScalarSet",
    "Severity",
    "SolverProvenance",
    "TableResult",
    "TetMesh3D",
    "TriMesh2D",
    "ValidityFlag",
    "Violation",
    "ViolationSet",
    "Waiver",
]
