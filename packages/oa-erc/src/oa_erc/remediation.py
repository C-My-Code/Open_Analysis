# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed remediation (ai-integration.md section 5) with a local adapter to ``oa_results``.

``oa_results.violations.Remediation`` still has the pre-addendum shape (``hint`` plus
``RemediationAction(item, field, value, note)``). Until the architecture owner decides on
``docs/change-requests/WP-201-remediation-model.md``, rules build the spec-shaped
:class:`TypedRemediation` and :meth:`TypedRemediation.to_model` encodes it losslessly:

- ``Remediation.hint`` is ``summary`` (and ``Violation.fix_hint`` is set to the same text);
- each typed action becomes one ``RemediationAction`` with ``field = kind``, ``note = description``
  and ``value`` = the full typed action as a JSON object;
- ``evidence`` becomes a trailing action with ``field = "evidence"``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from oa_model._base import OAModel
from oa_results.violations import Remediation, RemediationAction

ActionKind = Literal[
    "reroute",
    "move",
    "widen",
    "narrow",
    "change_spacing",
    "change_layer",
    "add_return_via",
    "add_stitching_vias",
    "add_decap",
    "move_decap",
    "backdrill",
    "change_stackup",
    "change_netclass",
    "change_rule",
    "add_waiver",
    "other",
]
AppliesVia = Literal["board_edit", "dru_rule", "netclass", "stackup", "setup", "waiver"]


class TypedAction(OAModel):
    kind: ActionKind = Field(description="Kind of change.")
    description: str = Field(description="One imperative sentence.")
    targets: tuple[str, ...] = Field(default=(), description="Item, net, layer ids or refdes.")
    parameter: str | None = Field(default=None, description="Parameter name, e.g. 'width'.")
    current_value: float | None = Field(default=None, description="Current value [SI].")
    suggested_value: float | None = Field(default=None, description="Suggested value [SI].")
    unit: str | None = Field(default=None, description="Unit of the values, e.g. 'm'.")
    applies_via: AppliesVia = Field(description="How the action can be applied.")
    confidence: Literal["low", "medium", "high"] = Field(
        default="medium", description="Confidence."
    )


class TypedRemediation(OAModel):
    summary: str = Field(description="One sentence; equals Violation.fix_hint.")
    actions: tuple[TypedAction, ...] = Field(description="At least one, by preference.")
    evidence: dict[str, JsonValue] = Field(default={}, description="At most 16 keys.")

    def to_model(self) -> Remediation:
        acts = [
            RemediationAction(
                item=a.targets[0] if a.targets else None,
                field=a.kind,
                value=a.model_dump(mode="json"),
                note=a.description,
            )
            for a in self.actions
        ]
        if self.evidence:
            acts.append(RemediationAction(field="evidence", value=dict(self.evidence)))
        return Remediation(hint=self.summary, actions=tuple(acts))

    @classmethod
    def from_model(cls, rem: Remediation) -> TypedRemediation:
        actions: list[TypedAction] = []
        evidence: dict[str, JsonValue] = {}
        for a in rem.actions:
            if a.field == "evidence" and isinstance(a.value, dict):
                evidence = dict(a.value)
            else:
                actions.append(TypedAction.model_validate(a.value))
        return cls(summary=rem.hint, actions=tuple(actions), evidence=evidence)
