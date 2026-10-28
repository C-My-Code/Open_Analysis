# WP-103: ReadOptions duplicate, backdrill stub length, KiCad reader lookup

Status: proposed. No contract was edited; local adapters are in place.

## 1. `ReadOptions` is defined twice
`oa_model.setup.ReadOptions` (WP-000B, per `WP-000B-undefined-referenced-types.md`) and `oa_framework.ingest.ReadOptions` are separate classes with identical fields. `oa_framework.load()` passes the framework class to `Reader.read()`. The readers of this WP (`oa-ingest-ipc2581`, `oa-kicad-bridge`) sit below `oa_framework` and annotate with the `oa_model.setup` class; they only read attributes, so both work, but a pydantic isinstance check would fail.
**Proposal:** `oa_framework.ingest` re-exports `oa_model.setup.ReadOptions` (as the 000B note already says). Impact: one import line in `oa_framework`.

## 2. IPC-2581 backdrill carries data `Backdrill` cannot hold
KiCad 10.0.5 writes backdrill as a `Spec` (`Backdrill type=START_LAYER | MUST_NOT_CUT_LAYER | MAX_STUB_LENGTH`) referenced by the via `Hole` through `SpecRef`. The reader maps START_LAYER and MUST_NOT_CUT_LAYER to `Backdrill(side, last_removed_layer, must_not_cut)` and reports `INGEST.BACKDRILL_SIZE_UNKNOWN`, because the file has no backdrill diameter (the via drill diameter is used).
`MAX_STUB_LENGTH` (0.1925 mm on the WP-102 SE2 via) has no field. **Proposal (minor bump 1.1):** `Backdrill.max_stub_length: Length | None = None`. Until then the value is dropped.

## 3. Reader lookup in the bridge
`AltiumReader` resolves the `kicad` reader through the `open_analysis.ingest` entry-point group (as specified). With `oa-ingest-kicad` not yet merged the delegation is tested with a stand-in reader; the round trip with a real Altium board is tested through `kicad-cli pcb export ipc2581` and `Ipc2581Reader` instead. No contract change needed; noted so WP-101 knows the bridge calls `reader_cls().read(path, options)`.
