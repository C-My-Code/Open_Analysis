<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Schema changelog

Schemas are generated from the `oa_model` pydantic models and committed here:

    python -m oa_model.schemas --out schemas           # regenerate
    python -m oa_model.schemas --out schemas --check   # CI: fail if out of date

Versioning policy: [board-model.md section 14](../docs/specs/board-model.md). A minor bump is
additive only and needs an ADR plus an entry below. A major bump needs an ADR and a migration in
`oa_model.migrations`. `oa-result.v1.json` is generated from `oa_results` (not by `oa_model`).

Every property carries a `description` stating its meaning and SI unit, so the schemas can be
handed directly to tools and AI agents. Cross-references use stable keys (net names, layer ids
`L1..Ln`, refdes, item ids), never list indices. Diagnostics carry `code`, `severity`, `message`,
`path` (RFC 6901 JSON pointer) and `hint`.

## board-model 1.0 and companions (initial)

- `board-model.v1.json` (`Board`, schema_version 1.0).
- `oa-physics.v1.json` (`oa-physics/1`), `oa-intent.v1.json` (`oa-intent/1`),
  `oa-setup.v1.json` (`oa-setup/1`), `oa-project.v1.json` (`oa-project/1`).
- `oa-xsection.v1.json` (`oa-xsection/1`), `oa-emproblem.v1.json` (`oa-emproblem/1`).
- `Diagnostic` has the optional additive fields `path` and `hint` beyond board-model.md section 4
  (see `docs/change-requests/WP-000B-structured-diagnostics.md`).
