# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Models of ``propose.rules`` and ``propose.apply`` (ai-integration.md sections 8.3, 10.3)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from oa_model._base import OAModel
from oa_service.models import ProjectHandle

Quantity = float | str
"""SI float (length [m], time [s]) or a string with unit such as '0.2mm' or '50ps'."""

ConstraintName = Literal[
    "clearance",
    "track_width",
    "length",
    "skew",
    "via_count",
    "diff_pair_gap",
    "diff_pair_uncoupled",
    "edge_clearance",
    "hole_clearance",
    "courtyard_clearance",
]
NetclassParam = Literal[
    "clearance", "track_width", "diff_pair_width", "diff_pair_gap", "via_diameter", "via_drill"
]


class NetclassParamChange(OAModel):
    """Set one parameter of a net class in the `.kicad_pro`."""

    kind: Literal["netclass_param"] = Field(description="Discriminator.")
    netclass: str = Field(description="Existing net class name.")
    param: NetclassParam = Field(description="Which net class parameter to set.")
    value: Quantity = Field(description="New value: SI float [m] or a string such as '0.2mm'.")


class AssignNetclassChange(OAModel):
    """Assign nets (glob patterns) to a net class in the `.kicad_pro`."""

    kind: Literal["assign_netclass"] = Field(description="Discriminator.")
    nets: tuple[str, ...] = Field(min_length=1, description="Net name glob patterns.")
    netclass: str = Field(description="Existing net class name.")


class CustomRuleChange(OAModel):
    """Add a custom rule to the `.kicad_dru` (an existing rule of the same name is replaced)."""

    kind: Literal["custom_rule"] = Field(description="Discriminator.")
    name: str = Field(min_length=1, description="Rule name, unique in the file.")
    condition: str = Field(description="KiCad rule condition, e.g. \"A.NetClass == 'HS'\".")
    constraint: ConstraintName = Field(description="KiCad constraint type.")
    min: Quantity | None = Field(default=None, description="Minimum: SI float [m or s] or string.")
    opt: Quantity | None = Field(default=None, description="Optimum: SI float [m or s] or string.")
    max: Quantity | None = Field(default=None, description="Maximum: SI float [m or s] or string.")
    layer: str | None = Field(default=None, description="Restrict the rule to one layer name.")
    severity: Literal["error", "warning", "ignore"] | None = Field(
        default=None, description="Severity of violations of this rule."
    )


class RemoveCustomRuleChange(OAModel):
    """Remove a custom rule from the `.kicad_dru`."""

    kind: Literal["remove_custom_rule"] = Field(description="Discriminator.")
    name: str = Field(description="Name of the rule to remove.")


RuleChange = Annotated[
    NetclassParamChange | AssignNetclassChange | CustomRuleChange | RemoveCustomRuleChange,
    Field(discriminator="kind"),
]


class FromViolations(OAModel):
    """Violations whose rule/netclass remediation actions become changes."""

    result_id: str = Field(description="Result id holding the violations.")
    fingerprints: tuple[str, ...] = Field(
        default=(), description="Violation fingerprints; empty means every non-waived violation."
    )


class ProposeIn(OAModel):
    """Compute a diff for `.kicad_dru` / `.kicad_pro` changes. Never writes user files."""

    project_id: str = Field(description="Project id from open_board.")
    from_violations: FromViolations | None = Field(
        default=None, description="Derive changes from violation remediations."
    )
    changes: tuple[RuleChange, ...] = Field(default=(), description="Explicit changes.")
    target: Literal["kicad_dru", "kicad_pro_netclasses", "auto"] = Field(
        default="auto", description="Restrict the proposal to one file kind."
    )


class FileChange(OAModel):
    """One file touched by the proposal."""

    path: str = Field(description="Absolute path of the file.")
    sha256_base: str | None = Field(description="sha256 of the current file; null if absent.")
    unified_diff: str = Field(description="Unified diff against the current content.")
    new_size: int = Field(ge=0, description="Size of the new content [B].")


class ChangeRationale(OAModel):
    """Why a change exists."""

    change_index: int = Field(ge=0, description="Index into `changes` [1].")
    fingerprints: tuple[str, ...] = Field(description="Violations that motivated the change.")
    rule_id: str | None = Field(description="Rule id of the violations, if any.")
    explanation: str = Field(description="One sentence.")


class NotApplicable(OAModel):
    """A remediation action that needs a human (board edit)."""

    fingerprint: str = Field(description="Violation fingerprint.")
    rule_id: str = Field(description="Rule id.")
    description: str = Field(description="What a human has to do.")
    applies_via: str = Field(description="How the action would be applied.")


class ChangeProposal(OAModel):
    """A dry-run proposal. Apply it with `propose.apply` and the confirm token."""

    proposal_id: str = Field(description="Proposal id; stored under .oa/proposals/.")
    project_id: str = Field(description="Project id.")
    files: tuple[FileChange, ...] = Field(description="Files that would change, with diffs.")
    changes: tuple[RuleChange, ...] = Field(description="Structured form of what the diff does.")
    rationale: tuple[ChangeRationale, ...] = Field(description="Why each change exists.")
    not_applicable: tuple[NotApplicable, ...] = Field(description="Board edits that need a human.")
    confirm_token: str = Field(description="Single-use token needed by propose.apply.")
    expires_utc: str = Field(description="Expiry (24 h after creation), ISO 8601 UTC.")
    text: str = Field(description="One-paragraph digest of the proposal.")


class ApplyIn(OAModel):
    """Apply a stored proposal. A WRITE: needs a process that allows writes."""

    proposal_id: str = Field(description="Proposal id from propose.rules.")
    confirm_token: str | None = Field(
        default=None, description="Single-use token from the proposal (required over MCP/REST)."
    )
    confirm: bool = Field(
        default=False,
        description="Human confirmation, honored only for the CLI and UI (`oa ... --yes`).",
    )


class AppliedFile(OAModel):
    """One written file."""

    file: str = Field(description="Absolute path.")
    sha256_before: str | None = Field(description="sha256 before; null if the file was new.")
    sha256_after: str = Field(description="sha256 of the written content.")
    backup: str | None = Field(description="Backup path; null if the file was new.")


class ApplyOut(OAModel):
    """Result of an applied proposal."""

    applied: tuple[AppliedFile, ...] = Field(description="Files written.")
    reload: ProjectHandle = Field(description="The re-opened project with new hashes.")
    text: str = Field(description="One-line digest.")


__all__ = [
    "AppliedFile",
    "ApplyIn",
    "ApplyOut",
    "AssignNetclassChange",
    "ChangeProposal",
    "ChangeRationale",
    "CustomRuleChange",
    "FileChange",
    "FromViolations",
    "NetclassParamChange",
    "NotApplicable",
    "ProposeIn",
    "RemoveCustomRuleChange",
    "RuleChange",
]
