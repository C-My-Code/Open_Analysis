# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""schemas/oa-result.v1.json is current (schema-compat check for oa_results)."""

from __future__ import annotations

from pathlib import Path

from oa_testkit import result_schema


def _schemas_dir() -> Path:
    for d in Path(__file__).resolve().parents:
        if (d / "schemas").is_dir():
            return d / "schemas"
    raise FileNotFoundError("schemas directory not found")


def test_result_schema_is_current(update_golden: bool) -> None:
    out = _schemas_dir()
    if update_golden:
        result_schema.main(["--out", str(out)])
    assert result_schema.main(["--out", str(out), "--check"]) == 0, (
        "run `python -m oa_testkit.result_schema --out schemas` and commit"
    )
