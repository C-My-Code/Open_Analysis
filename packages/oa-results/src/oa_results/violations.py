# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rule violations (interfaces.md section 3.7) and the ViolationSet payload (section 4.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import JsonValue

from oa_model._base import OAModel
from oa_model.common import ItemId, LayerId, NetId, Vec2
from oa_model.geometry import Polygon
from oa_results.container import PayloadBase

Severity = Literal["info", "warning", "error"]
_SEVERITIES: tuple[Severity, ...] = ("info", "warning", "error")


class Waiver(OAModel):
    fingerprint: str | None = None  # exact waiver
    rule_id: str | None = None  # or rule + nets glob
    nets: tuple[str, ...] = ()
    reason: str
    author: str | None = None
    expires: str | None = None  # ISO date


class RemediationAction(OAModel):
    """One machine-applicable suggestion: set ``field`` of ``item`` to ``value``."""

    item: ItemId | None = None
    field: str
    value: JsonValue
    note: str = ""


class Remediation(OAModel):
    """Optional structured fix suggestion (AI-oriented; ``fix_hint`` stays the human sentence)."""

    hint: str
    actions: tuple[RemediationAction, ...] = ()


class Violation(OAModel):
    rule_id: str  # stable across versions, e.g. "RP-001"
    severity: Severity
    message: str  # one sentence, human-readable
    nets: tuple[NetId, ...] = ()
    layers: tuple[LayerId, ...] = ()
    items: tuple[ItemId, ...] = ()  # board object refs (tracks, vias, pads, zones)
    location: Vec2  # marker point (board frame)
    geometry: Polygon | None = None  # highlight region
    measured: float | None = None
    limit: float | None = None
    unit: str | None = None  # "m", "s", "ohm", "Hz", ...
    fix_hint: str | None = None
    remediation: Remediation | None = None
    fingerprint: str  # stable hash (rule_id, sorted items, rounded location 10 um)
    waived: Waiver | None = None

    def digest(self) -> str:
        text = f"{self.rule_id} [{self.severity}] {self.message}"
        if self.measured is not None and self.limit is not None:
            unit = f" {self.unit}" if self.unit else ""
            text += f" (measured {self.measured:.4g} vs limit {self.limit:.4g}{unit})"
        return text

    def summary(self) -> dict[str, JsonValue]:
        """Compact JSON form for agents: identity, location, object refs, measured vs limit."""
        out: dict[str, JsonValue] = {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "digest": self.digest(),
            "location": [self.location[0], self.location[1]],
            "nets": list(self.nets),
            "layers": list(self.layers),
            "items": list(self.items[:20]),
            "measured": self.measured,
            "limit": self.limit,
            "unit": self.unit,
            "fingerprint": self.fingerprint,
            "waived": self.waived is not None,
        }
        hint = self.remediation.hint if self.remediation else self.fix_hint
        if hint is not None:
            out["remediation"] = hint
        return out


class ViolationSet(PayloadBase):
    kind: Literal["violations"] = "violations"
    rules_run: tuple[str, ...]
    violations: tuple[Violation, ...]
    counts: dict[Severity, int]

    @classmethod
    def from_violations(
        cls, rules_run: tuple[str, ...], violations: tuple[Violation, ...]
    ) -> ViolationSet:
        counts: dict[Severity, int] = dict.fromkeys(_SEVERITIES, 0)
        for v in violations:
            counts[v.severity] += 1
        return cls(rules_run=rules_run, violations=violations, counts=counts)

    def _digest(self) -> str:
        c = self.counts
        waived = sum(1 for v in self.violations if v.waived is not None)
        return (
            f"{len(self.violations)} violation(s) from {len(self.rules_run)} rule(s): "
            f"{c.get('error', 0)} error, {c.get('warning', 0)} warning, {c.get('info', 0)} info"
            f"; {waived} waived."
        )

    def _stats(self, max_items: int) -> dict[str, JsonValue]:
        order = {"error": 0, "warning": 1, "info": 2}
        worst = sorted(self.violations, key=lambda v: (order[v.severity], v.rule_id))[:max_items]
        by_rule: dict[str, JsonValue] = {}
        for v in self.violations:
            n = by_rule.get(v.rule_id, 0)
            assert isinstance(n, int)
            by_rule[v.rule_id] = n + 1
        return {
            "counts": {str(k): v for k, v in self.counts.items()},
            "by_rule": by_rule,
            "rules_run": list(self.rules_run[:max_items]),
            "top": [v.summary() for v in worst],
            "omitted": max(0, len(self.violations) - max_items),
        }
