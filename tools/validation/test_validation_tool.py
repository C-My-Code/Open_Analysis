# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: E402
"""Fast checks of the validation tool itself (run: ``uv run pytest tools/validation -q``).

These do not run any solver. They pin the counting rules (self-computed and [verify] cases never
count as independent passes) and the provenance fields of the published/cross-solver case files.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_page
import run_validation
from val_model import GOLDEN, Case


def _case(**kw: object) -> Case:
    base: dict[str, object] = {
        "id": "t.1",
        "category": "impedance/exact",
        "title": "t",
        "source": "s",
        "reference_class": "analytic",
        "independent": True,
        "quantity": "Z0",
        "unit": "ohm",
        "expected": 50.0,
        "got": 50.2,
        "tol_kind": "rel",
        "tol_value": 0.01,
    }
    base.update(kw)
    return Case(**base)  # type: ignore[arg-type]


def test_status_rules() -> None:
    assert _case().status == "pass"
    assert _case(got=51.0).status == "fail"
    assert _case(got=51.0, forced_status="known_gap").status == "known_gap"
    assert _case(got=50.0, forced_status="verify").status == "verify"
    assert _case(expected=None, got=None, forced_status="pending").status == "pending"
    assert _case(expected=None, got=None).status == "fail"  # a missing value never passes
    assert _case(got=float("nan")).status == "fail"
    assert _case(expected=99.0, got=99.5, tol_kind="min", tol_value=99.0).status == "pass"
    assert _case(expected=-25.0, got=-20.0, tol_kind="max", tol_value=-25.0).status == "fail"


def test_summary_counts_only_independent_passes() -> None:
    rows = [
        _case(id="a").to_json(),
        _case(id="b", reference_class="self-computed", independent=False).to_json(),
        _case(id="c", forced_status="verify").to_json(),
        _case(id="d", got=60.0).to_json(),
    ]
    (s,) = run_validation.summarize(rows)
    assert (s["cases"], s["passed"], s["failed"], s["verify"]) == (4, 2, 1, 1)
    assert s["independent_passed"] == 1


def test_page_builds_from_results(tmp_path: Path) -> None:
    rows = [_case(id="a").to_json(), _case(id="b", got=60.0).to_json()]
    doc = {
        "generated": "2026-01-01T00:00:00+00:00",
        "git": {"commit": "0" * 40, "branch": "x"},
        "platform": "test",
        "runtime_s": 1.0,
        "summary": run_validation.summarize(rows),
        "cases": rows,
    }
    res = tmp_path / "results.json"
    res.write_text(json.dumps(doc), encoding="utf-8")
    build_page.build(res, tmp_path / "out")
    index = (tmp_path / "out" / "index.md").read_text(encoding="utf-8")
    assert "never counted as independent validation" in index and "`b`" in index
    assert (tmp_path / "out" / "impedance.md").is_file()
    assert (tmp_path / "out" / "validation.html").is_file()


@pytest.mark.parametrize("suite", ["published", "crosssolver"])
def test_case_files_carry_provenance(suite: str) -> None:
    files = sorted((GOLDEN / suite).glob("*/case.toml"))
    assert files
    for f in files:
        data = tomllib.loads(f.read_text(encoding="utf-8"))
        assert {"source", "tolerance", "inputs", "expected", "meta"} <= data.keys(), f
        meta = data["meta"]
        assert meta["reference_class"] in (
            "analytic",
            "published",
            "cross-solver",
            "measured",
            "self-computed",
        )
        assert meta["citation_status"] in ("ok", "verify")
        if meta["citation_status"] == "verify":
            assert "[verify]" in data["source"] or "pending" in data["source"].lower(), f
        if meta["reference_class"] == "self-computed":
            assert meta["independent"] is False, f
