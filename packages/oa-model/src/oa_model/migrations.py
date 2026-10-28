# SPDX-License-Identifier: GPL-3.0-or-later
"""Schema migration scaffold (board-model.md section 14.1).

A major bump N -> N+1 registers `vN_to_vN1(dict) -> dict` in MIGRATIONS under key N. v1 is the
first major, so the registry is empty.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from oa_model.errors import UnsupportedSchemaVersion

SCHEMA_MAJOR = 1
SCHEMA_MINOR = 0
SCHEMA_VERSION = f"{SCHEMA_MAJOR}.{SCHEMA_MINOR}"

MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def parse_version(text: object) -> tuple[int, int]:
    """Parse 'MAJOR.MINOR'. Raises UnsupportedSchemaVersion when malformed."""
    try:
        major, minor = str(text).split(".")
        return int(major), int(minor)
    except ValueError as exc:
        raise UnsupportedSchemaVersion(f"malformed schema_version {text!r}") from exc


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Run the migration chain from the document's major up to SCHEMA_MAJOR."""
    major, _ = parse_version(data.get("schema_version"))
    if major > SCHEMA_MAJOR:
        raise UnsupportedSchemaVersion(
            f"schema_version {data['schema_version']} is newer than supported major {SCHEMA_MAJOR}"
        )
    while major < SCHEMA_MAJOR:
        step = MIGRATIONS.get(major)
        if step is None:
            raise UnsupportedSchemaVersion(f"no migration from schema major {major}")
        data = step(data)
        major += 1
    return data
