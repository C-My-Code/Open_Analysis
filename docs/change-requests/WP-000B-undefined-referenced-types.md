# WP-000B: types referenced by the specs but never defined

The specs name several types without defining them. oa_model needs them to be constructible, so the minimal definitions below were implemented. The owners should confirm or replace them.

| Type | Referenced from | Implemented in | Definition (minimal) |
|---|---|---|---|
| `ViaPad` | interfaces.md 5.1 `ViaBody.pads` | `oa_model.em` | `layer, radius, z_bottom, z_top` |
| `RegionProvenance` | 5.1 `Structure3D.provenance` | `oa_model.em` | `board_hash, nets, bbox, region: dict[str, JsonValue], cut_rules`; all optional. `Structure3D.provenance` therefore has a default (the spec has it required) |
| `FarFieldRequest`, `FieldDumpRequest` | 5.2 `OutputRequest` | `oa_model.em` | `frequencies`, `theta_step`/`phi_step`; `quantity` (E/H/J), `frequencies`, `plane`, `position` |
| `ReadOptions` | 3.2 and 6 `ProjectManifest` | `oa_model.setup.project` | exactly as in 3.2. `oa_framework` should re-export it, not redefine it |
| `Waiver` | 3.7 and 6 `AnalysisSetup` | `oa_model.setup.project` | exactly as in 3.7. `oa_model` is L0 and cannot import `oa_results`, so `oa_results.violations` should import `Waiver` from `oa_model.setup` |
| `JsonValue` | many | `pydantic.JsonValue` | used as is |

`ExtractionResult` and `BoundaryCrossing` are not oa_model types and were not created. `BC` was moved before `Boundaries` to avoid a forward reference.
