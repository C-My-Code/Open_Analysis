# WP-101: pad and via model points that needed a local adapter

None of these blocks the reader. Each is handled locally; the architecture owner may want to clarify the spec.

1. **Slots along local y.** `Drill.slot_length` is "along local x" only. KiCad allows `(drill oval 0.5 1.5)` (slot along y). The reader rotates the pad frame by +90 degrees (shapes, drill offset, `Pad.rotation += pi/2`) so the slot runs along x. Proposal: state this normalization in board-model.md section 8, or add `Drill.slot_angle`.
2. **`TrapezoidShape.delta_x/delta_y` sign convention.** The spec says "KiCad rect_delta semantics" but the model Y axis is flipped. The reader stores the raw `rect_delta` values (metres, signs as in the file). Proposal: define the polygon corners explicitly.
3. **`die_delay` unit.** The spec says seconds (KiCad 10). No demo board contains `die_delay`, so the file unit (assumed ps) is unverified.
4. **Arc length vs pcbnew.** pcbnew snaps arc centres to the 1 nm grid, so per-layer track length differs from the exact 3-point geometry by up to a few nm per arc (measured at most 3 nm). TASKS.md asks for 1e-9 relative; the golden test uses 1e-9 relative plus 5 nm per arc.
5. **Two `ReadOptions` classes.** `oa_framework.ingest.ReadOptions` and `oa_model.setup.ReadOptions` have identical fields (WP-000B notes say the framework should re-export). The reader reads options by attribute, so either works.
6. **Golden/expected format.** WP-102 ships `expected.yaml` (as TASKS.md says), not `expected.toml`. The ground-truth tests read the YAML.
