# oa-results

Result containers (types only; writers and readers arrive in WP-107 under `oa_results.io`).

Public API (interfaces.md sections 1.1, 3.7, 4.1): `ResultBundle`, `ResultItem`, `Provenance`,
`ArtifactRef`, payloads (`NetworkResult`, `FieldResult`, `TableResult`, `ViolationSet`,
`ProfileResult`, `ScalarSet`, `RLGCResultSet`, `ExtractionResult`, `FarFieldResult`,
`ArtifactOnly`), `RLGC`, `LineParams`, `ImpedanceResult`, `Violation`, `Waiver`,
`QualityReport`, `QualityCriteria`.

AI-facing additions (see `docs/change-requests/WP-000A-ai-fields.md`): every container has
`summary()` (size-bounded JSON dict) and `digest()` (short text), plus `accuracy_class`,
`validity_flags` and per-container solver provenance. `Violation` carries an optional structured
`remediation`.

`ResultBundle` uses aliases for `schema` and `summary`; dump with `by_alias=True`.

Golden suites: none (types only).
