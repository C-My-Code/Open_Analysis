# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compact ViolationSet summary: counts by rule and the worst N violations, within a byte budget.

``ViolationSet.summary()`` (oa_results) shrinks its ``top`` list until it fits, which leaves only
2 entries in 2 KiB for verbose violations (see docs/change-requests/WP-201-violationset-summary.md).
This local form keeps five entries by listing only the fields an agent needs to follow up.
"""

from __future__ import annotations

import json
from typing import Any

from oa_results.violations import ViolationSet

_ORDER = {"error": 0, "warning": 1, "info": 2}


def compact_summary(vs: ViolationSet, *, top: int = 5, max_bytes: int = 2048) -> dict[str, Any]:
    live = [v for v in vs.violations if v.waived is None]
    worst = sorted(live, key=lambda v: (_ORDER[v.severity], v.rule_id, v.fingerprint))[:top]
    by_rule: dict[str, int] = {}
    for v in vs.violations:
        by_rule[v.rule_id] = by_rule.get(v.rule_id, 0) + 1

    def build(hint_len: int, digits: int) -> dict[str, Any]:
        return {
            "digest": vs.digest(),
            "counts": dict(vs.counts),
            "by_rule": dict(sorted(by_rule.items())),
            "worst": [
                {
                    "rule_id": v.rule_id,
                    "severity": v.severity,
                    "nets": list(v.nets),
                    "layers": list(v.layers),
                    "at_mm": [round(v.location[0] * 1e3, 2), round(v.location[1] * 1e3, 2)],
                    "measured": None if v.measured is None else float(f"{v.measured:.{digits}g}"),
                    "limit": None if v.limit is None else float(f"{v.limit:.{digits}g}"),
                    "unit": v.unit,
                    "fix": (v.fix_hint or "")[:hint_len],
                    "fingerprint": v.fingerprint,
                }
                for v in worst
            ],
            "omitted": max(0, len(vs.violations) - len(worst)),
        }

    for hint_len, digits in ((70, 4), (40, 3), (0, 3)):
        out = build(hint_len, digits)
        if len(json.dumps(out, separators=(",", ":")).encode()) <= max_bytes:
            return out
    out = build(0, 3)
    while out["worst"] and len(json.dumps(out, separators=(",", ":")).encode()) > max_bytes:
        out["worst"].pop()
    return out
