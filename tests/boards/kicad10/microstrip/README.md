# microstrip

4-layer 1.6 mm board. Three 50 mm L1 microstrip traces (0.25/0.35/0.5 mm) over an L2 GND plane, h = 0.2 mm, no solder mask. Edge launch pads at both ends.

## Files

- `board.kicad_pcb`, `board.kicad_pro`: the board (KiCad 10 syntax, generated with KiCad 10.0.5).
- `expected.yaml`: ground truth for the readers and analyses (schema: `../expected.schema.json`).
- `drc.json`: `kicad-cli pcb drc --format json --severity-all` output.
- `board.ipc2581.xml`: IPC-2581 rev C export, `board.odb.zip`: ODB++ export, `board.png`: 3D render.

## Deliberate features and their rules

| Rule | Nets | Layer | Location (mm, file frame) | Created by |
|---|---|---|---|---|
| IM-002 | MS_250 | L1 | 35.000, 10.000 | `boardgen.features.microstrip` |
| IM-002 | MS_500 | L1 | 35.000, 30.000 | `boardgen.features.microstrip` |

## KiCad DRC

0 errors, 0 warnings (`drc.json`). By type: `{}`.

## Regenerate

```
python tools/boardgen/build_all.py --only microstrip
```

Licence: CC-BY-4.0 (see the `.license` sidecars).
