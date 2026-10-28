<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# oa-model

Neutral PCB board model for Open_Analysis (import name `oa_model`). Contract:
[docs/specs/board-model.md](../../docs/specs/board-model.md).

Public API (all re-exported from `oa_model`):

- Models: `Board` and its parts, `Points`/`Floats`/`NDArray`, materials, `CrossSection`,
  `Structure3D`/`EMProblem`, reserved `QSProblem`/`ThermalProblem`.
- `parse_quantity`, `validate_board` (structured `Diagnostic`s), `BoardIndex`.
- `apply_physics` (sidecar overlay), `resolve_intent`, setup schemas (`DesignIntent`, ports,
  `ProjectManifest`, `AnalysisSetup`).
- `load_board` / `dump_board` for `.oab.json` and `.oab`; `Board.content_hash()`.
- Shipped material library: `oa_model/data/materials.yaml`.

Regenerate the JSON Schemas with `python -m oa_model.schemas --out schemas` (use `--check` in CI).
Every model field carries a description with units so the schemas are self-documenting for tools
and AI agents.

Test: `uv run --project packages/oa-model pytest`.
