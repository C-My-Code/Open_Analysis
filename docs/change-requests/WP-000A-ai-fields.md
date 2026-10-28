<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->
# WP-000A: AI-oriented fields and clarifications in interfaces.md

Status: implemented in `oa_results` / `oa_framework` as optional or additive changes. The
architecture owner should fold them into `docs/specs/interfaces.md`.

## Problem

The suite must be usable by AI agents (MCP server `oa_mcp`, optional `oa_assistant`). Agents
need bounded, self-describing results, trust metadata and structured errors. interfaces.md
section 4.1 has none of the first two, and section 12 does not say how errors are shaped.

## Additions (all optional, backwards compatible)

1. **`ResultContainer` base** (`oa_results.container`) for every payload (`NetworkResult`,
   `FieldResult`, `TableResult`, `ViolationSet`, `ProfileResult`, `ScalarSet`, `RLGCResultSet`,
   `ImpedanceResult`, `FarFieldResult`, `ExtractionResult`, `ArtifactOnly`) and `ResultBundle`:
   - `summary(max_chars=4000, max_items=20) -> dict[str, JsonValue]`: size-bounded (the serialized
     form never exceeds `max_chars`), keys `kind`, `digest`, `accuracy_class`, `validity_flags`,
     `provenance`, `stats`. List caps shrink first, then `stats` becomes `{"truncated": true}`.
   - `digest() -> str`: at most 240 characters of plain text.
   - `accuracy_class: "reference" | "validated" | "screening" | "estimate" | "unverified" | None`.
   - `validity_flags: tuple[ValidityFlag(code, ok, message), ...]`.
2. **Payload provenance**: `PayloadBase.provenance: SolverProvenance | None` with `solver`,
   `solver_version`, `inputs_hash`. The bundle-level `Provenance` gains `inputs_hash`.
3. **Violation** already has `rule_id`, `severity`, `location`, `items`/`nets`/`layers`,
   `measured`/`limit`/`unit` and `fix_hint`. Added `remediation: Remediation | None`
   (structured: `hint` plus machine-applicable `RemediationAction(item, field, value, note)`),
   `Violation.summary()` and `Violation.digest()`, and `ViolationSet.from_violations()`.
4. **Structured errors** (`oa_framework.errors`): `ErrorInfo{code, message, path, hint}`,
   `FrameworkError` (carries the same four fields and `.info()`), and `to_error_info(exc)`
   for any exception. `JobStatus.error` is an `ErrorInfo`. `oa_model.errors.OAError` subclasses
   should expose optional `path` and `hint` attributes so `to_error_info` can read them
   (request to WP-000B; not required).

## Clarifications of existing signatures

- `ResultBundle.summary: str` collides with the new `summary()` method and with
  `BaseModel` conventions. The field is `summary_text` with alias `summary`; the wire name is
  unchanged. Likewise `schema` is `schema_` with alias `schema` (it shadows `BaseModel.schema`).
  Dump with `model_dump(by_alias=True)` / `model_dump_json(by_alias=True)`.
- `QualityCriteria` and `QualityReport` are defined in `oa_results.quality`, not
  `oa_network.quality`, because `NetworkResult.quality` is required and `oa_results` (L1)
  cannot import `oa_network` (L2). `oa_network.quality` re-exports them.
- `Severity` is defined in `oa_results.violations`; `oa_framework.rules` re-exports it.
- `TableResult.rows` is optional when `inline` is given (a small table needs no Parquet file).
- `MatrixPassivity`, `PortMapEntry`, `ProfileEvent`, `Scalar` were referenced but not defined;
  minimal definitions are in `oa_results`.
- `ArtifactStore` (RunContext) and `ToolInfo`/`CancelToken` (ExternalRunner) were referenced
  but not defined; minimal protocols/models are in `oa_framework`.
- `BoardGeometry` is `Any` in `oa_framework._types` until WP-104 provides `oa_geometry`.
- `oa_testkit` is declared L1 but `harness.run_inline` needs `oa_framework` (L3). The
  import-linter config therefore treats it as test-support: no product package may import it,
  and it is not part of the layer stack.
- `oa_mcp` sits with `oa_cli`/`oa_server`; `oa_assistant` is above them and nothing may import it.
