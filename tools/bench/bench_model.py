# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Case loading, target evaluation and status rules for the benchmark harness (WP-304).

Results schema ``oa-bench/1``; case schema ``oa-bench-case/1`` (see README.md).
"""

from __future__ import annotations

import statistics
import tomllib
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
CASES_DIR = REPO / "tests" / "benchmarks" / "cases"
RESULTS_DIR = REPO / "tests" / "benchmarks" / "results"
SCHEMA = "oa-bench/1"
CASE_SCHEMA = "oa-bench-case/1"

# Only "pass" counts as passed. "ready" means a runner exists but was not run in this batch.
STATUSES = ("pass", "fail", "pending", "ref-pending", "verify", "ready", "skipped")
# status values that a sub-case without a runner may declare
DECLARED = ("pending", "ref-pending", "verify")


def load_cases(root: Path = CASES_DIR) -> dict[str, dict[str, Any]]:
    """All ``<ID>/case.toml`` files, keyed by id and sorted."""
    out: dict[str, dict[str, Any]] = {}
    for f in sorted(root.glob("*/case.toml")):
        with f.open("rb") as fh:
            data = tomllib.load(fh)
        if data.get("schema") != CASE_SCHEMA:
            raise ValueError(f"{f}: schema must be {CASE_SCHEMA}")
        if data["id"] != f.parent.name:
            raise ValueError(f"{f}: id {data['id']} does not match its directory")
        out[data["id"]] = data
    return out


def declared_status(sub: dict[str, Any], runners: set[str]) -> str | None:
    """Status of a sub-case that cannot run (no runner, or a runner that is not registered)."""
    if sub.get("pending") in DECLARED:
        return str(sub["pending"])
    if not sub.get("runner") or sub["runner"] not in runners:
        return "pending"
    return None


def evaluate(targets: dict[str, Any], m: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Compare measurements with one device's targets. Returns (status, checks).

    A target that is absent is not gated and produces no check. A measurement that is missing
    for a gated target fails (a missing value never passes).
    """
    checks: list[dict[str, Any]] = []

    def add(name: str, value: float | None, limit: float, unit: str) -> None:
        ok = value is not None and value <= limit
        checks.append({"name": name, "value": value, "limit": limit, "unit": unit, "ok": ok})

    if "accuracy_max" in targets:
        add(
            "accuracy",
            m.get("accuracy"),
            float(targets["accuracy_max"]),
            str(targets.get("accuracy_unit", "")),
        )
    if "time_max_s" in targets:
        per = targets.get("time_per", "run")
        key = "time_per_unit_s" if per != "run" else "time_median_s"
        add(f"time per {per}", m.get(key), float(targets["time_max_s"]), "s")
    if "mem_max_mb" in targets:
        add("host peak memory", m.get("peak_rss_mb"), float(targets["mem_max_mb"]), "MB")
    if "gpu_mem_max_mb" in targets:
        add("GPU peak memory", m.get("gpu_peak_mb"), float(targets["gpu_mem_max_mb"]), "MB")
    if not checks:
        return "pass", checks  # nothing gated: recorded for information
    return ("pass" if all(c["ok"] for c in checks) else "fail"), checks


def summarize_times(warm: list[float], units: int) -> dict[str, Any]:
    med = statistics.median(warm) if warm else None
    return {
        "time_warm_s": warm,
        "time_median_s": med,
        "time_per_unit_s": (med / units) if (med is not None and units > 0) else None,
    }


def case_status(sub_statuses: list[str]) -> str:
    """Case level: pass only when every sub-case passes; otherwise the least advanced state."""
    s = set(sub_statuses)
    if s == {"pass"}:
        return "pass"
    if "fail" in s:
        return "fail"
    if "pass" in s:
        return "partial"
    for k in ("ready", "ref-pending", "pending", "verify", "skipped"):
        if k in s:
            return k
    return "pending"
