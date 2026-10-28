# WP-000B: structured diagnostics and self-documenting schemas

Status: proposed (implemented locally, additive). Source: coordinator requirement "AI agents (MCP and JSON)".

## Problem
`Diagnostic` (board-model.md section 4) has `severity, code, message, item, location, layer`. Agents need a machine-locatable position and a remedy.

## Change implemented
Two optional, additive fields on `Diagnostic` (minor-compatible, default `None`):
- `path: str | None`: RFC 6901 JSON pointer into the board document (for example `/tracks/12/layer`).
- `hint: str | None`: suggested fix, phrased as an action.

`validate_board` fills both for every `MODEL.*` finding. Item references in `item` are stable keys (item id, net name, refdes, layer id), never indices. A JSON pointer necessarily contains a list index; it is a locator for the current document, not a cross-reference.

Every model field now has `Field(description=...)` with units, enforced by `tests/test_schemas.py`. The type aliases `Length`, `Frequency`, `NetId`, `LayerId` and so on carry the unit text so the generated `schemas/*.json` are self-documenting.

## Impact
- `schemas/board-model.v1.json` gains `path` and `hint` on `Diagnostic`. This is additive, so schema_version stays 1.0 (v1.0 is not yet frozen: WP-000 has not merged).
- board-model.md section 4 should list the two fields when the spec is frozen.

## Index-based references in the spec
None of the model cross-references use list indices (nets by name, layers by `L1..Ln` id, components by refdes, pads and padstacks by id). One note: `LayerId` values `L<n>` and `D<n>` encode stack position, so inserting a copper layer renumbers ids. That is by spec (section 4) and is left as is.
