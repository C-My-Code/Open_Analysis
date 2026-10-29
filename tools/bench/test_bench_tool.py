# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: E402
"""Self-test of the benchmark harness (run: ``uv run pytest tools/bench -q``).

Uses synthetic cases only: no solver, no GPU, no subprocess.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bench_model as bm
import bench_report
import run as bench_run


def _sub(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "s",
        "title": "t",
        "runner": "synthetic",
        "devices": ["cpu", "cuda"],
        "targets": {
            "cpu": {
                "accuracy_max": 0.05,
                "time_max_s": 0.02,
                "time_per": "section",
                "mem_max_mb": 200,
            },
            "cuda": {"time_max_s": 5.0, "gpu_mem_max_mb": 4096},
        },
    }
    base.update(kw)
    return base


CASE = {"id": "B-X-01", "group": "impedance", "title": "x", "reference": {"class": "analytic"}}


def test_every_benchmarks_id_has_a_case_file() -> None:
    spec = (bm.REPO / "docs" / "BENCHMARKS.md").read_text(encoding="utf-8")
    ids = {
        line.split("|")[1].strip()
        for line in spec.split("## 3.")[0].splitlines()
        if line.startswith("| B-")
    }
    assert ids and ids == set(bm.load_cases())


def test_case_files_are_well_formed() -> None:
    for cid, c in bm.load_cases().items():
        assert c["targets_text"] and c["reference"]["class"], cid
        for s in c["sub"]:
            for d in s["devices"]:
                assert d in s["targets"], (cid, s["id"], d)
            if s.get("pending"):
                assert s["pending"] in bm.DECLARED and s.get("owner"), (cid, s["id"])


def test_evaluate_gates_and_missing_values_fail() -> None:
    t = _sub()["targets"]["cpu"]
    ok = {"accuracy": 0.01, "time_per_unit_s": 0.01, "peak_rss_mb": 150.0}
    assert bm.evaluate(t, ok)[0] == "pass"
    assert bm.evaluate(t, ok | {"accuracy": 0.06})[0] == "fail"
    assert bm.evaluate(t, ok | {"time_per_unit_s": 0.03})[0] == "fail"
    assert bm.evaluate(t, ok | {"peak_rss_mb": 250.0})[0] == "fail"
    assert bm.evaluate(t, {"accuracy": 0.01})[0] == "fail"  # missing value never passes
    assert bm.evaluate(t, ok | {"accuracy": float("nan")})[0] == "fail"


def test_case_status_never_overstates() -> None:
    assert bm.case_status(["pass", "pass"]) == "pass"
    assert bm.case_status(["pass", "pending"]) == "partial"
    assert bm.case_status(["pass", "fail"]) == "fail"
    assert bm.case_status(["pending", "ref-pending"]) == "ref-pending"
    assert bm.case_status(["ready", "pending"]) == "ready"


def _entry(sub: dict[str, Any], device: str = "cpu", **kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "runners": {"synthetic"},
        "allow_heavy": False,
        "gpu_present": False,
        "reps": 1,
        "timeout_s": 1.0,
    }
    args.update(kw)
    return bench_run.run_entry({**CASE, "sub": [sub]}, sub, device, **args)


def test_pending_ready_and_no_device_are_never_pass() -> None:
    assert _entry(_sub(pending="ref-pending", owner="WP-510"))["status"] == "ref-pending"
    assert _entry(_sub(runner=""))["status"] == "pending"
    assert _entry(_sub(runner="unregistered"))["status"] == "pending"
    r = _entry(_sub(), "cuda")
    assert r["status"] == "skipped" and r["reason"] == "skipped (no device)"
    assert _entry(_sub(heavy=True))["status"] == "ready"


def test_measured_entry_is_evaluated(monkeypatch: pytest.MonkeyPatch) -> None:
    good = {"accuracy": 0.01, "time_per_unit_s": 0.01, "peak_rss_mb": 100.0, "units": 5}
    monkeypatch.setattr(bench_run, "_spawn", lambda *a, **k: good)
    assert _entry(_sub())["status"] == "pass"
    monkeypatch.setattr(bench_run, "_spawn", lambda *a, **k: good | {"accuracy": 1.0})
    assert _entry(_sub())["status"] == "fail"
    err = {"error": {"code": "bench.runner_failed", "message": "boom", "path": "p", "hint": ""}}
    monkeypatch.setattr(bench_run, "_spawn", lambda *a, **k: err)
    r = _entry(_sub())
    assert r["status"] == "fail" and r["reason"] == "boom"


def test_select_by_case_group_and_device() -> None:
    cases = {"B-X-01": {**CASE, "sub": [_sub(id="a"), _sub(id="b", devices=["cpu"])]}}
    assert len(bench_run.select(cases, None, None, None, "cpu")) == 2
    assert len(bench_run.select(cases, None, None, None, "cuda")) == 1
    assert bench_run.select(cases, ["B-Y"], None, None, "cpu") == []
    assert bench_run.select(cases, None, ["em"], None, "cpu") == []
    assert len(bench_run.select(cases, None, None, ["b"], "cpu")) == 1


def _doc(commit: str, t: float, acc: float, status: str = "pass") -> dict[str, Any]:
    rec = {
        "case": "B-IMP-01",
        "sub": "sections",
        "device": "cpu",
        "group": "impedance",
        "status": status,
        "targets": {"accuracy_max": 0.05, "time_max_s": 0.02, "time_per": "run"},
        "owner": "",
        "reason": "",
        "measured": {
            "accuracy": acc,
            "time_median_s": t,
            "time_cold_s": t,
            "peak_rss_mb": 100.0,
            "threads": 8,
        },
    }
    return {
        "schema": bm.SCHEMA,
        "generated": "2026-01-01",
        "_file": f"{commit}.json",
        "machine": {"commit": commit, "cpu": "c", "logical_cores": 2, "ram_gb": 1, "gpu": None},
        "profile": {"name": "bench", "threads_env": "8", "mem_gb": 16, "cpu_load_check": "skipped"},
        "results": [rec],
    }


def test_regression_thresholds() -> None:
    old = [_doc("a" * 8, 0.010, 0.010)]
    assert bench_report.regressions(_doc("b" * 8, 0.0119, 0.010), old) == []
    assert len(bench_report.regressions(_doc("b" * 8, 0.0121, 0.010), old)) == 1  # > 20 % slower
    assert bench_report.regressions(_doc("b" * 8, 0.010, 0.0224), old) == []
    assert len(bench_report.regressions(_doc("b" * 8, 0.010, 0.0226), old)) == 1  # > 25 % of tol
    failed_before = [_doc("a" * 8, 0.001, 0.0, status="fail")]
    assert bench_report.regressions(_doc("b" * 8, 1.0, 1.0), failed_before) == []  # no passing base


def test_pages_build_from_committed_results(tmp_path: Path) -> None:
    res = tmp_path / "results"
    res.mkdir()
    for i, c in enumerate(("a" * 8, "b" * 8)):
        (res / f"2026-01-0{i + 1}-{c}-h.json").write_text(
            json.dumps(_doc(c, 0.01 * (i + 1), 0.01)), encoding="utf-8"
        )
    out = tmp_path / "bm"
    bench_report.build(res, out)
    index = (out / "index.md").read_text(encoding="utf-8")
    assert "B-IMP-01" in index and "B-PERF-01" in index  # every case id is listed
    page = (out / "impedance.md").read_text(encoding="utf-8")
    assert "**pass**" in page and list((out / "plots").glob("*.svg"))
    assert "pending" in (out / "em.md").read_text(encoding="utf-8")


def test_empty_results_directory_still_builds(tmp_path: Path) -> None:
    bench_report.build(tmp_path / "none", tmp_path / "bm")
    assert "No benchmark results" in "\n".join(bench_report.performance_section(tmp_path / "none"))


def test_profiles_match_benchmarks_md() -> None:
    import bench_profile as bp

    assert bp.PROFILES["bench"] == {"threads": 8, "mem_gb": 16, "gpu_mem_gb": 12}
    assert bp.PROFILES["default"]["threads"] == 2 and bp.PROFILES["default"]["mem_gb"] == 8
