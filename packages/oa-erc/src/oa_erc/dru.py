# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``.kicad_dru`` suggestions from violations (research 06 section 3.2, ai-integration 10.3).

:func:`propose_dru` is pure: it returns a :class:`DruProposal` (new text, unified diff, base
hash) and never touches a file. :func:`apply_dru` is the only writer; it refuses unless
``allow_writes`` is set, the base file is unchanged (sha256) and ``dry_run`` is false. It writes
atomically and keeps a backup. Only remediation actions with ``applies_via == "dru_rule"`` are
exported. Board edits stay human actions.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path

from pydantic import Field

from oa_erc.remediation import TypedRemediation
from oa_model._base import OAModel
from oa_results.violations import Violation

# KiCad constraint name -> (option, unit suffix, scale from SI)
_CONSTRAINTS: dict[str, tuple[str, str, float]] = {
    "edge_clearance": ("min", "mm", 1e3),
    "clearance": ("min", "mm", 1e3),
    "track_width": ("min", "mm", 1e3),
    "diff_pair_gap": ("min", "mm", 1e3),
    "diff_pair_uncoupled": ("max", "mm", 1e3),
    "skew": ("max", "ps", 1e12),
}
HEADER = "(version 1)\n"


class DruProposal(OAModel):
    path: str = Field(description="Target .kicad_dru path (nothing is written by proposing).")
    text: str = Field(description="Proposed full file content.")
    unified_diff: str = Field(description="Diff against the current file content.")
    sha256_base: str | None = Field(description="sha256 of the current file; None if absent.")
    rule_names: tuple[str, ...] = Field(description="Names of the rules added, sorted.")
    dry_run: bool = Field(default=True, description="Always true: proposals are not applied.")


def _rule_text(name: str, net: str, constraint: str, value: float) -> str:
    opt, unit, scale = _CONSTRAINTS[constraint]
    return (
        f'(rule "{name}"\n'
        f"  (condition \"A.NetName == '{net}'\")\n"
        f"  (constraint {constraint} ({opt} {value * scale:.4g}{unit})))\n"
    )


def propose_dru(
    violations: Iterable[Violation], path: str | Path, *, existing: str | None = None
) -> DruProposal:
    """Suggested DRC rules for the dru_rule actions of ``violations`` (waived ones skipped).

    ``existing`` is the current file text (read it yourself; None = no file). Rules whose name
    is already present are not added again."""
    base = existing if existing is not None else ""
    rules: dict[str, str] = {}
    for v in violations:
        if v.waived is not None or v.remediation is None:
            continue
        rem = TypedRemediation.from_model(v.remediation)
        for a in rem.actions:
            if a.applies_via != "dru_rule" or a.parameter not in _CONSTRAINTS:
                continue
            if a.suggested_value is None:
                continue
            for net in a.targets or v.nets:
                name = f"OA_{v.rule_id}_{a.parameter}_{net}".replace(" ", "_")
                if f'(rule "{name}"' in base:
                    continue
                rules.setdefault(name, _rule_text(name, net, a.parameter, a.suggested_value))
    body = base if base.strip() else HEADER
    if not body.endswith("\n"):
        body += "\n"
    text = body + "".join(rules[k] for k in sorted(rules))
    diff = "".join(
        difflib.unified_diff(
            base.splitlines(keepends=True),
            text.splitlines(keepends=True),
            fromfile="a/" + Path(path).name,
            tofile="b/" + Path(path).name,
        )
    )
    return DruProposal(
        path=str(path),
        text=text,
        unified_diff=diff,
        sha256_base=hashlib.sha256(base.encode()).hexdigest() if existing is not None else None,
        rule_names=tuple(sorted(rules)),
    )


class DruWriteRefused(RuntimeError):
    """Raised by :func:`apply_dru` when a safety condition is not met."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def apply_dru(
    proposal: DruProposal, *, allow_writes: bool = False, dry_run: bool = True
) -> Path | None:
    """Write the proposal. Dry run by default; needs ``allow_writes=True`` and ``dry_run=False``.

    Returns the written path (None on a dry run). Raises :class:`DruWriteRefused` with code
    ``WRITES_DISABLED`` or ``STALE_BASE``. A backup ``<name>.bak`` is kept next to the file."""
    if dry_run:
        return None
    if not allow_writes:
        raise DruWriteRefused("WRITES_DISABLED", "writing requires allow_writes=True")
    target = Path(proposal.path)
    current = target.read_text(encoding="utf-8") if target.exists() else None
    cur_hash = hashlib.sha256(current.encode()).hexdigest() if current is not None else None
    if cur_hash != proposal.sha256_base:
        raise DruWriteRefused("STALE_BASE", f"{target.name} changed since the proposal")
    if target.exists():
        shutil.copy2(target, target.with_name(target.name + ".bak"))
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(proposal.text)
    os.replace(tmp, target)
    return target
