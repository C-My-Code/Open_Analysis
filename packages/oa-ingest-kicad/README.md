# oa-ingest-kicad

Native reader for KiCad `.kicad_pcb` (+ `.kicad_pro`) files (WP-101, ADR-0006). It produces an
`oa_model.Board` without KiCad installed and never imports `pcbnew`.

## Public API

```python
from pathlib import Path
from oa_ingest_kicad import KiCadReader, KiCadIngestError
from oa_model.setup import ReadOptions

board = KiCadReader().read(Path("board.kicad_pcb"), ReadOptions())
```

- `KiCadReader` implements the `Reader` protocol (interfaces.md 3.2): `name = "kicad"`,
  `extensions = (".kicad_pcb",)`, `can_read(path)` (sniffs the first 4 KB), `read(path, options)`.
  Registered as entry point `kicad` in group `open_analysis.ingest`.
- `ReadOptions`: `project_file` (default: `<stem>.kicad_pro`, else the only `.kicad_pro` in the
  directory), `arc_tolerance` (default 1 um), `strict` (any warning/error diagnostic raises).
  `refill_zones` belongs to `oa_kicad_bridge` and is ignored here.
- `KiCadIngestError` (`IngestError`): fatal failures with `code`, `path`, `hint` (and
  `diagnostics` in strict mode). Everything else is a `Diagnostic` on `Board.diagnostics` with
  `code`, `path` (RFC 6901 pointer) and `hint`.
- `oa_ingest_kicad.sexpr`: the tolerant tokenizer / tree builder. `(pts (xy ..) ..)` lists
  (zone fills, outlines) are parsed straight into `(N, 2)` numpy arrays.

## Contracts implemented

board-model.md v1.0 (all of Board), interfaces.md 3.2 (Reader), ai-integration.md 6 and 13
(structured diagnostics with `path` and `hint`).

## Format support

| KiCad | `(version)` | Status |
|---|---|---|
| 7.0 | 20221018 | supported (`tstamp` ids, `fp_text` reference/value) |
| 8.0 | 20240108 .. 20240706 | supported |
| 9.0 | 20241030, 20241229 | supported, top-level `(net N "name")` table; verified on demos |
| 9.99 / 10 dev | 20250513, 20250907 | supported, verified on demos |
| 10.0 | 20260206 | supported, name-only nets; verified on demos and WP-102 boards |
| older than 20221018 | | warning `INGEST.VERSION_OLD`, best effort |
| newer than 2026xxxx | | warning `INGEST.VERSION_NEWER`, unknown tokens ignored |

Unknown tokens inside the known contexts (footprint, pad, via, track, zone, setup, stackup) are
aggregated into `INGEST.UNKNOWN_TOKEN` info diagnostics. They never fail a read.

## Mapping notes

- mm to m, `y_oa = -y_kicad`, degrees to radians. Origin untouched.
- Copper layers are renumbered `L1..Ln` from the physical stack order (stackup), never from the KiCad
  ordinals. Without a usable stackup, KiCad's default is used (`known=False`,
  `INGEST.STACKUP_DEFAULTED`). `addsublayer` plies become separate dielectric slabs (`dielectric 3/2`).
- Pads: position is footprint-relative in the stored frame and is transformed to the board frame;
  the stored pad angle is already absolute and is used as is. Slots running along local y are
  re-expressed with the pad frame rotated by 90 degrees (the model only has slots along local x).
  `die_delay` is assumed to be stored in ps.
- Trapezoid `delta_x/delta_y` carry the raw `rect_delta` values (metres, signs as in the file).
- Footprint-owned zones are read in board coordinates (KiCad stores them absolute).
- Vias: kind is derived from the span (through/blind/buried) and `micro` only when declared and
  spanning two adjacent layers. `padstack` modes `normal`, `front_inner_back`, `custom` are expanded to
  one entry per layer; a size of 0 means no copper on that layer.
- Backdrill: `(backdrill ..)` and `(secondary_drill ..)` are the top side, `(tertiary_drill ..)` the
  bottom side. **pcbnew verification (KiCad 10.0.5, `tools/verify_backdrill.py`):** the end layer
  named in `(layers "F.Cu" "In2.Cu")` is itself cut (`IsBackdrilledOrPostMachined` is true for F.Cu,
  In1.Cu and In2.Cu). So `last_removed_layer` is the named end layer and `must_not_cut` is the next
  layer inward. This matches IPC-2581's `MUST_NOT_CUT` on the WP-102 `via_transition` board.
- Net classes: explicit `netclass_assignments` (lowest `priority` number wins), else the first matching
  `netclass_patterns` glob, else `Default`. `target_z_*` is parsed from the class name (`50ohm`, `90Ohm-diff_CSI`, `100R`).
- Diff pairs: suffix heuristic `_DP/_DN`, `_P/_N`, `_T/_C`, `+/-`, `P/N` (case-insensitive).
- Zones: fractured `filled_polygon` rings are re-unioned (`shapely.make_valid`) to recover holes.
  `INGEST.ZONE_UNFILLED` and `stale_suspected` (a fill that overlaps copper of another net) are
  reported. Text on copper layers is skipped (`INGEST.COPPER_TEXT_SKIPPED`).
- Outline: Edge.Cuts items (board and footprint) are chained with a 1 um tolerance; nesting depth
  decides board / cutout / extra board. No closed loop gives `INGEST.OUTLINE_MISSING` and the copper bounding box.

## Tests and golden suites

`uv run pytest packages/oa-ingest-kicad`

- `tests/test_units.py`: hand-written snippets (pad rotation on a rotated back-side footprint, 3-point arcs,
  fractured polygons, v9 vs v10 net forms, stackup flattening, vias, backdrill, zones, outline chaining).
- `tests/test_synthetic.py`: WP-102 boards (`tests/boards/kicad10/*/expected.yaml`): counts, stackup z to 1 nm, nets, diff pairs, backdrill.
- `tests/test_demos.py` (marker `kicad`, skipped without the demos): every demo board parses, no `error`
  diagnostic, `validate_board(strict=True)`; Jetson performance; golden comparison with pcbnew.
- Golden suite `tests/golden/ingest_kicad/{cm5_minima,pic_programmer,video}/case.toml`, generated once with
  `tools/pcbnew_extract.py` (run with KiCad's Python) and `tools/make_case.py`. Counts exact; track length
  1e-9 relative (arcs: 5 nm each, pcbnew snaps arc centres to 1 nm); zone-fill area 0.1 %; z 1 nm; pad
  poses 1e-9 m / rad. The stackup z reference is computed from the raw file text by an independent scanner, because
  SWIG cannot read the stackup.

## Zone-fill repair and validation (WP-509)

`fix_fractured` repairs KiCad's bridged zone fills with `make_valid(method="structure")` (13x
faster than GEOS linework). The result is accepted only when its area equals the ring's
shoelace area; otherwise `linework` runs, as before. `tests/test_make_valid_equivalence.py`
shows the same point set, polygon count and hole count on every WP-102 board and the cm5 and
pic_programmer demos. Ring start vertices differ, so `content_hash()` of boards with fractured
fills changed once (no committed golden holds such a hash). `validate_board` runs the compiled
ring predicates when `oa-native` is installed.

## Measured performance (Windows 11, this development machine)

| Board | Size | Time |
|---|---|---|
| jetson-agx-thor-baseboard | 88 MB | 9.9 s, peak RSS 1.01 GiB (limit 30 s / 4 GB) |
| vme-wren | 72 MB | 15 s |
| video | 6 MB | 2.4 s |
| cm5_minima | 3.5 MB | 1.1 s |

All 19 demo boards (KiCad 10.0.5 install) load with no `error` diagnostics and pass `validate_board(strict=True)`.
The peak RSS figure comes from `test_jetson_performance` (Windows peak working set) and is an upper bound
(the test process may already have parsed other boards). Reference-machine timing is still to be confirmed (TASKS open question 2).
