# ADR-0003: Neutral board model as frozen pydantic v2 models, SI metres, versioned JSON / packed container

- Status: Accepted (2026-10-27)

## Context
- Every analysis, every reader and the UI share one board description.
- Readers reparsing `.kicad_pcb` independently is the main source of fragmentation in the open-source ecosystem (research 06 §2.3).
- The model must be immutable, serializable and schema-described. It must also cope with ~100 MB boards.
- KiCad and Altium lack physics data: frequency-dependent Dk/Df, roughness, conductivity and thermal data.

## Decision
- The model is `oa_model`, specified in [board-model.md](../specs/board-model.md):
  - **pydantic v2 `BaseModel`, `frozen=True`, `extra="forbid"`**, with tuples for collections;
  - custom immutable `Points`/`Floats`/`NDArray` array types for bulk coordinates;
  - JSON Schema generated and committed.
- We chose this over frozen dataclasses for the JSON Schema output (UI forms, TS types, REST), boundary validation and discriminated unions. Hot-path readers may use `model_construct` and then run `validate_board` once.
- SI units (metres, Hz, S/m, K). The frame is X right, Y up as seen from the top, with z = 0 at the bottom of the stack.
- Polygons with holes are discretized. Track centrelines and outline edges keep analytic arcs.
- Physics data lives in a **sidecar** `<project>.oa-physics.yaml`, merged by `apply_physics`. Intent, ports, loads and rule configs live in **versioned YAML setups** (`oa/intent.yaml`, `oa/setups/*.yaml`), not in `Board`.
- Two file forms: `.oab.json` (canonical, diffable) and `.oab` (zip + npy arrays). A content hash over a canonical encoding is the root of every cache key.
- Schema versioning is `MAJOR.MINOR`:
  - a minor bump is additive only, and newer-minor files load with pruning plus a warning;
  - a major bump needs a migration;
  - every change needs an ADR, and CI checks schema compatibility.
- The derived products (`ReferenceMap`, unions, topology) are **not** in the model. They are cached in `oa_geometry`.

## Consequences
- v1.0 is frozen at the end of WP-000, which unblocks every Wave 1 package.
- The model carries no shapely objects, so `oa_model` stays light (pydantic + numpy + pyyaml).
- Physics overlays change the board hash, which correctly invalidates solver caches.
