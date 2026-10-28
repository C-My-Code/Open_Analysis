# via_transition

8-layer 2.0 mm board. SE1: L1->L6 signal over a through via with a 0.62 mm stub (VI-001). SE2: the same via backdrilled from the bottom to L7. VD_P/VD_N: through-via pair L1->L8 with 4 return vias. NR: an L1->L8 transition without any return via (RP-004). Plus one blind, one buried and one micro via.

## Files

- `board.kicad_pcb`, `board.kicad_pro`: the board (KiCad 10 syntax, generated with KiCad 10.0.5).
- `expected.yaml`: ground truth for the readers and analyses (schema: `../expected.schema.json`).
- `drc.json`: `kicad-cli pcb drc --format json --severity-all` output.
- `board.ipc2581.xml`: IPC-2581 rev C export, `board.odb.zip`: ODB++ export, `board.png`: 3D render.

## Deliberate features and their rules

| Rule | Nets | Layer | Location (mm, file frame) | Created by |
|---|---|---|---|---|
| VI-001 | SE1 | L6 | 15.000, 15.000 | `boardgen.features.via_transition` |
| RP-004 | NR | L1 | 55.000, 35.000 | `boardgen.features.via_transition` |

## Notes

- KiCad 10.0.5 exports SE2's backdrill to IPC-2581 as Spec BD_1B: START_LAYER B.Cu, MUST_NOT_CUT_LAYER In5.Cu (= L6, the last used layer), MAX_STUB_LENGTH 0.1925 mm (observed value). pcbnew reports the end layer In6.Cu (L7) as itself cut (IsBackdrilledOrPostMachined true on In6.Cu and B.Cu).

## KiCad DRC

0 errors, 3 warnings (`drc.json`). By type: `{"warning": {"via_dangling": 3}}`.

## Regenerate

```
python tools/boardgen/build_all.py --only via_transition
```

Licence: CC-BY-4.0 (see the `.license` sidecars).
