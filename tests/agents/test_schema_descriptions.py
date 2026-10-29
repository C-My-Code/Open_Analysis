# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Schema description lint (ai-integration 7.1): every property is described, numerics carry a unit.

Walks `schemas/*.json` and every command input/output schema of the registry. A numeric property
needs a `[unit]` in its description unless it is marked `x-oa-unitless` (or is an integer count
described with `[1]`). Gaps that already exist in schemas owned by other WPs are recorded in
`schema_lint_baseline.txt`: the baseline may shrink, never grow (a fixed gap must be removed from
it, so the lint keeps ratcheting). Regenerate with `OA_UPDATE_SCHEMA_LINT=1 pytest <this file>`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from oa_service import Registry
from oa_service.testing import FakeService

REPO = Path(__file__).resolve().parents[2]
BASELINE = Path(__file__).with_name("schema_lint_baseline.txt")


def _walk(node: Any, path: str, out: list[tuple[str, dict[str, Any]]]) -> None:
    if isinstance(node, dict):
        for name, prop in (node.get("properties") or {}).items():
            if isinstance(prop, dict):
                out.append((f"{path}/{name}", prop))
                _walk(prop, f"{path}/{name}", out)
        for key in ("$defs", "definitions"):
            for name, sub in (node.get(key) or {}).items():
                _walk(sub, f"{path}#{name}", out)
        for key in ("anyOf", "oneOf", "allOf"):
            for sub in node.get(key) or []:
                _walk(sub, path, out)
        if isinstance(node.get("items"), dict):
            _walk(node["items"], path, out)
    return None


def _kinds(prop: dict[str, Any]) -> set[Any]:
    kinds = {prop.get("type")}
    for alt in prop.get("anyOf", []) + prop.get("oneOf", []):
        if isinstance(alt, dict):
            kinds.add(alt.get("type"))
    return kinds


def lint(doc: dict[str, Any], label: str) -> list[str]:
    props: list[tuple[str, dict[str, Any]]] = []
    _walk(doc, label, props)
    problems: list[str] = []
    for path, prop in props:
        described = "description" in prop or "$ref" in prop or "allOf" in prop or "const" in prop
        if (not described and not any(k in prop for k in ("anyOf", "oneOf"))) or (
            not described
            and not any(isinstance(a, dict) and "$ref" in a for a in prop.get("anyOf", []))
        ):
            problems.append(f"{path}: no description")
        if _kinds(prop) & {"number"} and not prop.get("x-oa-unitless"):
            desc = str(prop.get("description", ""))
            if "[" not in desc:
                problems.append(f"{path}: numeric property without a [unit]")
    return problems


def _baseline_lines() -> list[str]:
    if not BASELINE.is_file():
        return []
    return BASELINE.read_text(encoding="utf-8").splitlines()


def _check(problems: list[str], scope: str) -> None:
    """Compare ``problems`` of one scope (a name prefix) with the baseline."""
    base = {line for line in _baseline_lines() if line.startswith(scope)}
    if os.environ.get("OA_UPDATE_SCHEMA_LINT"):
        rest = [line for line in _baseline_lines() if not line.startswith(scope)]
        text = "\n".join(sorted({*rest, *problems})) + "\n"
        BASELINE.write_text(text, encoding="utf-8", newline="\n")
        return
    new = sorted(set(problems) - base)
    fixed = sorted(base - set(problems))
    assert new == [], "new schema description gaps:\n" + "\n".join(new[:40])
    assert fixed == [], "gaps fixed; remove them from the baseline:\n" + "\n".join(fixed[:40])


def test_published_schema_files_are_described() -> None:
    problems: list[str] = []
    files = sorted((REPO / "schemas").glob("*.json"))
    assert files
    for f in files:
        problems += lint(json.loads(f.read_text(encoding="utf-8")), "file:" + f.name)
    _check(problems, "file:")


def test_command_schemas_are_described(tmp_path: Path) -> None:
    reg = Registry.load(FakeService(workdir=tmp_path / "w"))
    problems: list[str] = []
    for d in reg.definitions():
        problems += lint(d.input_type.model_json_schema(by_alias=True), f"cmd:{d.spec.name}.in")
        problems += lint(
            d.output_type.model_json_schema(by_alias=True, mode="serialization"),
            f"cmd:{d.spec.name}.out",
        )
    _check(problems, "cmd:")
