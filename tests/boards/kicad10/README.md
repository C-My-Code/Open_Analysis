# Synthetic KiCad 10 test boards (WP-102)

Eleven small boards with known geometry, written by `tools/boardgen/` and refilled, checked and exported with
KiCad 10.0.5. They are the ground truth for the KiCad reader (WP-101), the IPC-2581 reader (WP-103), the ERC rules
(WP-201), impedance/profile/DC/EM tests and the web viewer. All are FR-4-like (Dk 4.3, Df 0.02), 35 um copper, no
solder mask, with a known stackup. Licence: CC-BY-4.0 (`*.license` sidecars); the generator is GPL-3.0-or-later.

| Directory | Layers | Purpose |
|---|---|---|
| `microstrip` | 4 (1.6 mm) | 3 L1 microstrips (0.25/0.35/0.5 mm, 50 mm, h = 0.2 mm), launch pads; IM-002 on the 0.25 and 0.5 mm traces |
| `microstrip_v9` | 4 | Same copper in KiCad 9 syntax (net table, numeric item nets, version 20241229) |
| `stripline` | 6 | Symmetric stripline on L3, offset stripline on L4 (L3 empty above, L5 GND below) |
| `diff_pair` | 6 | 100 ohm pair: L1 microstrip, blind via pair + 4 return vias, L3 stripline; DP-001/002/003 |
| `cpwg` | 4 | CPWG (S 0.3, G 0.15, h 0.2) with two via fences (RF-001 on the 3x pitch) |
| `split_plane` | 4 | RP-001/002/003/007, ED-001/002 |
| `via_transition` | 8 (2.0 mm) | Stub (VI-001), backdrill, via pair with returns, transition without return (RP-004), blind/buried/micro vias |
| `power_plane` | 4 | L3 +1V0 plane 40 x 30 mm, VRM U5, load U1 (32 pins), decaps C1 (1.5 mm) and C2 (12 mm): PD-001; EM-011 cavity |
| `power_strip` | 4 | 50 x 5 mm strip, analytic R = Rs L / W |
| `power_two_layer_vias` | 4 | Two 20 x 20 mm squares joined by 9 vias, analytic via-array R |
| `combined` | 8 | Everything above in separate regions, net classes `50ohm`/`90ohm`/`100ohm`, `USB_D+`/`USB_D-` |

Every directory holds `board.kicad_pcb`, `board.kicad_pro`, `board.ipc2581.xml` (IPC-2581 rev C),
`board.odb.zip`, `board.png` (3D render), `drc.json`, `expected.yaml` and a `README.md`.

## Regenerate

```
python tools/boardgen/build_all.py                 # all boards (needs KiCad 10.0.5; set OA_KICAD_DIR if not in the default place)
python tools/boardgen/build_all.py --only cpwg     # one board
python tools/boardgen/validate_expected.py         # schema + consistency check (needs pyyaml, jsonschema)
```

Output is deterministic: uuids come from `uuid5(board, kind, counter)` and timestamps are scrubbed, so two runs
give byte-identical `board.kicad_pcb`, `board.kicad_pro`, `drc.json`, `expected.yaml` and `README.md`. The IPC-2581 and
ODB++ exports are KiCad output whose element order (pads within a package, for example) varies from run to run; they
are equal as data, not as bytes, so compare them by parsing. `.gitattributes` here disables line-ending conversion.

## `expected.yaml` (schema: `expected.schema.json`, id `oa-board-expected/1`)

All physical quantities are SI (m, m^2, Hz, S/m, ohm). Exceptions, named `*_mm`: positions and tolerances in millimetres
in the **KiCad file frame (Y down)**; the Y-up board model uses `y_model = -y_file`. Layer ids are canonical (`L1` = top
copper, `D1` = first dielectric, top to bottom); `source_layer`/`source_name` give the KiCad name. z = 0 is the bottom face
of the lowest copper layer.

| Key | Content |
|---|---|
| `kicad` | syntax flavour (10/9), file version, whether a net table exists |
| `materials` | Dk, Df, copper thickness, conductivity (5.8e7 S/m, the model default) |
| `counts` | nets, vias per kind (`through`/`blind`/`buried`/`micro`), pads (smd/tht), tracks, arcs, zones, rule areas (copper-pour keepouts), footprints, copper layers. Measured by pcbnew after the refill and asserted equal to the generator's own count |
| `stackup` | `board_thickness` and per-layer `thickness`, `z_bottom`, `z_top` (exact to 1 nm) |
| `copper_area` | per layer, per net, m^2, computed by pcbnew after `ZONE_FILLER` (union of fills, tracks, pads, via pads); tolerance 0.1 % |
| `nets`, `net_classes`, `net_class_patterns`, `diff_pairs` | net names, `.kicad_pro` classes/patterns, pair membership (`origin`: `naming`) |
| `geometry` | named dimensions per feature (widths, lengths, gaps, via spans, stub lengths, plane outlines, ...) |
| `erc` | violations that MUST be reported: `rule_id`, `nets`, `layer`, `location_mm` (null = whole-net result), `tolerance_mm`, `generator` (the `boardgen.features.*` function that created the feature) and `why` |
| `must_not_report` | catalogue rules that must not fire anywhere on the board |
| `negative_cases` | specific places that look similar but are compliant (a test may assert no report there) |
| `unspecified` | cases where the result depends on rule thresholds; tests must not assert either way |
| `intended_drc` | KiCad DRC **errors** that are deliberate (empty on every board) |
| `drc` | counts and types of all DRC results, including warnings |
| `dc` | analytic resistances with formula and tolerance (`power_strip`, `power_two_layer_vias`) |
| `em` | EM-011 cavity frequencies and suggested ports (`power_plane`) |

## Using KiCad on these boards

DRC: `kicad-cli pcb drc --format json --severity-all board.kicad_pcb` returns exit code 0 and 0 errors on every board.
Remaining warnings are `via_dangling` (isolated buried/blind/micro vias that exist only to exercise the reader and the
swiss-cheese antipads). Loading a board with `pcbnew.LoadBoard` from Python can block on a wx assertion dialog for
some inputs (seen with the KiCad 9 syntax file); always run such helpers with a timeout.
