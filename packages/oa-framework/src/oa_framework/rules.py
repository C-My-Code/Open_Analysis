# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rule protocol for ERC and EMI (interfaces.md section 3.5)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar, Literal, Protocol, runtime_checkable

from oa_framework._types import BoardGeometry
from oa_framework.services import Services
from oa_model._base import OAModel
from oa_model.board import Board
from oa_model.common import NetId
from oa_model.setup import ResolvedIntent
from oa_results.violations import Severity, Violation

RuleCategory = Literal[
    "return_path",
    "edge",
    "impedance",
    "diff_pair",
    "via",
    "crosstalk",
    "timing",
    "power",
    "rf",
    "emi",
]


class RuleContext(Protocol):
    board: Board
    intent: ResolvedIntent

    def geometry(self) -> BoardGeometry: ...

    def services(self) -> Services: ...

    def nets_in_scope(self) -> tuple[NetId, ...]:
        """After the rule's NetSelector and intent."""
        ...


@runtime_checkable
class Rule(Protocol):
    id: ClassVar[str]  # "RP-001"
    title: ClassVar[str]
    category: ClassVar[RuleCategory]
    aliases: ClassVar[tuple[str, ...]]  # e.g. ("E2",)
    default_severity: ClassVar[Severity]
    requires: ClassVar[frozenset[str]]  # service names, e.g. {"impedance"}
    references: ClassVar[tuple[str, ...]]  # citations for thresholds
    Config: ClassVar[type[OAModel]]  # thresholds; MUST have defaults for every field

    def check(self, ctx: RuleContext, cfg: OAModel) -> Iterable[Violation]: ...


__all__ = ["Rule", "RuleCategory", "RuleContext", "Severity"]
