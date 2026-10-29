# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Editor for ``net_settings`` of a ``.kicad_pro`` file (JSON). Key order and other content kept."""

from __future__ import annotations

import json
from typing import Any

from oa_service.errors import ServiceError, close_matches

#: RuleChange.param -> key in a net class record (values in mm).
PARAM_KEYS = {
    "clearance": "clearance",
    "track_width": "track_width",
    "diff_pair_width": "diff_pair_width",
    "diff_pair_gap": "diff_pair_gap",
    "via_diameter": "via_diameter",
    "via_drill": "via_drill",
}


def load(text: str) -> dict[str, Any]:
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ServiceError(
            "MODEL_INVALID", f".kicad_pro is not valid JSON: {exc.msg}", hint="Fix the file."
        ) from exc
    if not isinstance(doc, dict):
        raise ServiceError("MODEL_INVALID", ".kicad_pro must hold a JSON object.")
    return doc


def dump(doc: dict[str, Any]) -> str:
    """KiCad's own style: two-space indent, trailing newline, non-ASCII kept."""
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def _classes(doc: dict[str, Any]) -> list[dict[str, Any]]:
    ns = doc.setdefault("net_settings", {})
    classes = ns.setdefault("classes", [])
    return [c for c in classes if isinstance(c, dict)]


def set_netclass_param(doc: dict[str, Any], netclass: str, param: str, value_mm: float) -> None:
    classes = _classes(doc)
    for c in classes:
        if c.get("name") == netclass:
            c[PARAM_KEYS[param]] = round(value_mm, 6)
            return
    names = [str(c.get("name")) for c in classes]
    raise ServiceError(
        "NOT_FOUND",
        f"Net class {netclass!r} is not in the .kicad_pro.",
        path="/changes",
        hint="Existing classes: " + (", ".join(names) or "none") + ".",
        details={"candidates": close_matches(netclass, names)},
    )


def assign_netclass(doc: dict[str, Any], patterns: list[str], netclass: str) -> None:
    names = [str(c.get("name")) for c in _classes(doc)]
    if netclass not in names:
        raise ServiceError(
            "NOT_FOUND",
            f"Net class {netclass!r} is not in the .kicad_pro.",
            path="/changes",
            hint="Existing classes: " + (", ".join(names) or "none") + ".",
            details={"candidates": close_matches(netclass, names)},
        )
    ns = doc["net_settings"]
    pats = ns.setdefault("netclass_patterns", [])
    for p in patterns:
        if not any(e.get("pattern") == p and e.get("netclass") == netclass for e in pats):
            pats.append({"netclass": netclass, "pattern": p})
