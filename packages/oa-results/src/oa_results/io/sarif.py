# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""SARIF 2.1.0 export of a ``ViolationSet`` (interfaces.md section 4.2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from oa_model.board import Board
from oa_results.violations import Severity, ViolationSet

__all__ = ["SARIF_SCHEMA_URI", "sarif_document", "to_sarif"]

SARIF_SCHEMA_URI = "https://json.schemastore.org/sarif-2.1.0.json"
_LEVEL: dict[Severity, str] = {"info": "note", "warning": "warning", "error": "error"}


def sarif_document(
    violations: ViolationSet,
    board: Board | str | Path | None = None,
    *,
    tool_version: str = "0.1.0",
) -> dict[str, Any]:
    """SARIF log as a dict. ``board`` names the physical location (a ``Board`` or a file name)."""
    if isinstance(board, Board):
        uri = board.meta.source_file or f"{board.meta.name}.oab.json"
    elif board is None:
        uri = "board.oab.json"
    else:
        uri = Path(board).as_posix()
    rule_ids = list(
        dict.fromkeys([*violations.rules_run, *(v.rule_id for v in violations.violations)])
    )
    results: list[dict[str, Any]] = []
    for v in violations.violations:
        logical: list[dict[str, str]] = [{"name": n, "kind": "net"} for n in v.nets]
        logical += [{"name": i, "kind": "member"} for i in v.items]
        props: dict[str, Any] = {
            "location_mm": [v.location[0] * 1e3, v.location[1] * 1e3],
            "layers": list(v.layers),
            "fingerprint": v.fingerprint,
        }
        for key in ("measured", "limit", "unit"):
            val = getattr(v, key)
            if val is not None:
                props[key] = val
        res: dict[str, Any] = {
            "ruleId": v.rule_id,
            "ruleIndex": rule_ids.index(v.rule_id),
            "level": _LEVEL[v.severity],
            "message": {"text": v.message},
            "locations": [
                {
                    "physicalLocation": {"artifactLocation": {"uri": uri}},
                    "logicalLocations": logical,
                }
            ],
            "partialFingerprints": {"oaFingerprint/v1": v.fingerprint},
            "properties": props,
        }
        hint = v.remediation.hint if v.remediation else v.fix_hint
        if hint:
            props["remediation"] = hint
        if v.waived is not None:
            res["suppressions"] = [{"kind": "external", "justification": v.waived.reason}]
        results.append(res)
    return {
        "$schema": SARIF_SCHEMA_URI,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Open_Analysis",
                        "version": tool_version,
                        "rules": [{"id": r} for r in rule_ids],
                    }
                },
                "artifacts": [{"location": {"uri": uri}}],
                "results": results,
            }
        ],
    }


def to_sarif(violations: ViolationSet, board: Board | str | Path | None, path: str | Path) -> Path:
    """Write ``violations`` as SARIF 2.1.0 to ``path``."""
    p = Path(path)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(sarif_document(violations, board), indent=1, ensure_ascii=False))
        fh.write("\n")
    return p
