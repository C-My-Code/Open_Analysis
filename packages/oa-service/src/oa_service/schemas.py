# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""JSON Schemas generated from the catalog (ai-integration.md section 7.2).

``oa schema --list`` and ``schemas/commands/`` are both produced here, and CI diffs the
regeneration (:func:`check_schemas`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from oa_model._base import OAModel
from oa_service.errors import ErrorInfo, ServiceError, close_matches
from oa_service.models import SchemaEntry
from oa_service.types import Envelope

if TYPE_CHECKING:
    from oa_service.commands import Registry

__all__ = [
    "check_schemas",
    "file_for",
    "schema_entries",
    "schema_for",
    "schema_models",
    "write_schemas",
]


def file_for(schema_id: str) -> str:
    """Relative path under ``schemas/`` of a schema id ('oa.x/1' -> 'commands/oa.x.v1.json')."""
    name = schema_id.replace("/", ".v") + ".json"
    return name if schema_id.startswith("oa-") else f"commands/{name}"


def schema_models(registry: Registry) -> dict[str, type[OAModel]]:
    """Schema id -> model for the envelope, the error record, and every command input/output."""
    out: dict[str, type[OAModel]] = {"oa-envelope/1": Envelope, "oa-error/1": ErrorInfo}
    for d in registry.definitions():
        out[d.spec.input_model] = d.input_type
        if d.spec.output_model.startswith("oa."):  # oa-setup/1 etc. are published by oa_model
            out[d.spec.output_model] = d.output_type
    return dict(sorted(out.items()))


def schema_for(registry: Registry, schema_id: str) -> dict[str, Any]:
    models = schema_models(registry)
    if schema_id not in models:
        cands = close_matches(schema_id, models)
        raise ServiceError(
            "NOT_FOUND",
            f"No schema {schema_id!r}.",
            path="/id",
            hint="List schemas with `oa schema --list`. Close matches: "
            + (", ".join(cands) or "none")
            + ".",
            details={"candidates": cands},
        )
    doc = models[schema_id].model_json_schema(by_alias=True)
    doc = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": schema_id, **doc}
    return doc


def schema_entries(registry: Registry) -> tuple[SchemaEntry, ...]:
    models = schema_models(registry)
    return tuple(
        SchemaEntry(id=i, title=m.__name__, file=f"schemas/{file_for(i)}")
        for i, m in models.items()
    )


def _render(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _with_plugins(registry: Registry) -> Registry:
    """The registry plus the ``open_analysis.commands`` entry points (WP-210's propose.*)."""
    from oa_service.commands import Registry as _Registry

    return _Registry.load(registry.service)


def write_schemas(registry: Registry, root: Path) -> list[Path]:
    """Write every schema under ``root`` (the repo's ``schemas/`` directory)."""
    registry = _with_plugins(registry)
    written = []
    for sid in schema_models(registry):
        path = root / file_for(sid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render(schema_for(registry, sid)), encoding="utf-8", newline="\n")
        written.append(path)
    return written


def check_schemas(registry: Registry, root: Path) -> list[str]:
    """Differences between ``root`` and the regeneration (empty when in sync)."""
    registry = _with_plugins(registry)
    problems = []
    expected = {
        file_for(sid): _render(schema_for(registry, sid)) for sid in schema_models(registry)
    }
    for rel, text in expected.items():
        p = root / rel
        if not p.is_file():
            problems.append(f"missing {rel}")
        elif p.read_text(encoding="utf-8") != text:
            problems.append(f"stale {rel}")
    cmd_dir = root / "commands"
    if cmd_dir.is_dir():
        for p in sorted(cmd_dir.glob("*.json")):
            if f"commands/{p.name}" not in expected:
                problems.append(f"orphan commands/{p.name}")
    return problems


def main(argv: list[str] | None = None) -> int:
    """``python -m oa_service.schemas [--check] [DIR]``: write or verify the published schemas."""
    import argparse
    from typing import cast

    from oa_service.commands import Registry
    from oa_service.service import OAService

    ap = argparse.ArgumentParser(prog="python -m oa_service.schemas")
    ap.add_argument("dir", nargs="?", default="schemas", help="the repository schemas/ directory")
    ap.add_argument("--check", action="store_true", help="fail if the files are out of date")
    ns = ap.parse_args(argv)
    registry = Registry(cast(OAService, None))  # the catalog needs no service to describe itself
    root = Path(ns.dir)
    if ns.check:
        problems = check_schemas(registry, root)
        for p in problems:
            print(p)
        return 1 if problems else 0
    print(f"wrote {len(write_schemas(registry, root))} schemas under {root}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
