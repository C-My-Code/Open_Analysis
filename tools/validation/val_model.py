# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Result record, tolerance logic and case.toml loading for the validation runner (WP-302)."""

from __future__ import annotations

import math
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "tests" / "golden"

# reference classes: where the expected value comes from
CLASSES = ("analytic", "published", "cross-solver", "measured", "self-computed")
# statuses; only "pass" counts as passed, "verify", "pending" and "skipped" never count
STATUSES = ("pass", "fail", "known_gap", "verify", "pending", "skipped")


@dataclass
class Case:
    """One comparison of a computed value against a reference."""

    id: str
    category: str
    title: str
    source: str
    reference_class: str
    independent: bool  # False: the reference was produced by this project's own code
    quantity: str
    unit: str
    expected: float | None
    got: float | None
    tol_kind: str  # "rel" | "abs" | "min" | "max" | "exact"
    tol_value: float
    case_dir: str = ""
    note: str = ""
    forced_status: str | None = None  # "verify" | "pending" | "skipped" | "known_gap"
    runtime_s: float | None = None
    error_pct: float | None = field(default=None, init=False)
    error_abs: float | None = field(default=None, init=False)
    status: str = field(default="pending", init=False)

    def __post_init__(self) -> None:
        assert self.reference_class in CLASSES, self.reference_class
        self.evaluate()

    def evaluate(self) -> None:
        e, g = self.expected, self.got
        ok = False
        if e is not None and g is not None and math.isfinite(g):
            self.error_abs = g - e
            if e != 0:
                self.error_pct = 100.0 * (g - e) / abs(e)
            ok = self._within(e, g)
        if self.forced_status in ("verify", "pending", "skipped"):
            self.status = self.forced_status
        elif ok:
            self.status = "pass"
        elif self.forced_status == "known_gap":
            self.status = "known_gap"
        else:
            self.status = "fail"

    def _within(self, e: float, g: float) -> bool:
        if self.tol_kind == "rel":
            return abs(g - e) <= self.tol_value * abs(e) + 1e-300
        if self.tol_kind == "abs":
            return abs(g - e) <= self.tol_value
        if self.tol_kind == "min":  # one-sided bound: got >= tol_value (expected is the bound)
            return g >= self.tol_value
        if self.tol_kind == "max":  # got <= tol_value
            return g <= self.tol_value
        return g == e

    @property
    def tolerance_text(self) -> str:
        if self.tol_kind == "rel":
            return f"{self.tol_value * 100:g} %"
        if self.tol_kind == "abs":
            return f"{self.tol_value:g} {self.unit}".strip()
        if self.tol_kind == "min":
            return f">= {self.tol_value:g} {self.unit}".strip()
        if self.tol_kind == "max":
            return f"<= {self.tol_value:g} {self.unit}".strip()
        return "exact"

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("forced_status")
        d["tolerance"] = {
            "kind": self.tol_kind,
            "value": self.tol_value,
            "text": self.tolerance_text,
        }
        return d


def load_case(suite: str, name: str) -> dict[str, Any]:
    """Read ``tests/golden/<suite>/<name>/case.toml``."""
    with (GOLDEN / suite / name / "case.toml").open("rb") as fh:
        return tomllib.load(fh)


def case_dir(suite: str, name: str) -> str:
    return f"tests/golden/{suite}/{name}"
