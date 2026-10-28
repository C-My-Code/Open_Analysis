# SPDX-License-Identifier: GPL-3.0-or-later
"""Quasi-static extraction problem types, reserved for M2 (interfaces.md section 5.4)."""

from typing import Literal

from pydantic import Field, JsonValue

from oa_model._base import OAModel
from oa_model.arrays import Floats
from oa_model.common import NetId
from oa_model.em import EMTerminalGeom, Structure3D


class QSTerminal(OAModel):
    name: str = Field(description="Terminal name.")
    net: NetId = Field(description="Net name.")
    source: EMTerminalGeom | None = Field(default=None, description="R/L: current injection face.")
    sink: EMTerminalGeom | None = Field(default=None, description="R/L: current return face.")


class QSProblem(OAModel):
    schema: Literal["oa-qsproblem/1"] = Field(  # type: ignore[assignment]
        default="oa-qsproblem/1", description="Schema tag; always 'oa-qsproblem/1'."
    )
    structure: Structure3D = Field(description="Geometry and materials.")
    terminals: tuple[QSTerminal, ...] = Field(description="Terminals.")
    want: frozenset[Literal["C", "G", "R", "L"]] = Field(description="Matrices to extract.")
    freqs: Floats | None = Field(default=None, description="Frequencies for R(f), L(f), Hz.")
    ground_net: NetId | None = Field(default=None, description="Ground net name.")
    discretization: dict[str, JsonValue] = Field(default={}, description="Adapter hints.")
