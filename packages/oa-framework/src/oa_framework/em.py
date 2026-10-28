# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Solver adapter interface (interfaces.md section 5.3)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar, Literal, Protocol, runtime_checkable

from pydantic import JsonValue

from oa_framework.external import ExternalCommand, ExternalResult, ToolInfo
from oa_model._base import OAModel
from oa_model.common import Diagnostic
from oa_model.em import EMProblem
from oa_results.artifact import ArtifactRef
from oa_results.extraction import FarFieldResult
from oa_results.network import NetworkResult
from oa_results.tables import TableResult


class AdapterCapabilities(OAModel):
    physics: frozenset[str]  # {"fullwave_driven"} ...
    ports: frozenset[str]  # {"lumped", "wave", "coax"}
    wave_port_mode: Literal["modal", "tl_probe", "none"]
    boundaries: frozenset[str]
    dispersive_dielectric: Literal["broadband", "single_frequency_fit", "none"]
    surface_roughness: bool
    sheet_conductors: bool
    excitations_per_run: Literal["one", "all"]  # openEMS: one; Palace: all
    farfield: bool
    platforms: frozenset[str]  # {"windows", "linux", "macos"}


class PreparedRun(OAModel):
    workdir: Path
    commands: tuple[ExternalCommand, ...]  # one per excitation when excitations_per_run == "one"
    mesh_stats: dict[str, JsonValue]  # cells/DOF, min cell, est memory (GB), est time
    files: tuple[str, ...]


class EMSolveOutput(OAModel):
    network: NetworkResult | None  # quality NOT yet assessed: oa_em fills it
    port_impedance: TableResult | None
    farfield: FarFieldResult | None
    fields: tuple[ArtifactRef, ...] = ()
    convergence_log: TableResult | None
    solver_version: str
    warnings: tuple[Diagnostic, ...] = ()


@runtime_checkable
class SolverAdapter(Protocol):
    name: ClassVar[str]  # "openems", "palace", "emerge"
    tool: ClassVar[str]  # ExternalCommand.program used

    def capabilities(self) -> AdapterCapabilities: ...

    def check(self, problem: EMProblem) -> tuple[Diagnostic, ...]:
        """Unsupported features are reported as errors."""
        ...

    def prepare(self, problem: EMProblem, workdir: Path, tool: ToolInfo) -> PreparedRun: ...

    def collect(
        self, problem: EMProblem, run: PreparedRun, results: Sequence[ExternalResult]
    ) -> EMSolveOutput: ...
