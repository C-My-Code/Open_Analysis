# cpwg

4-layer board. Two 40 mm L1 CPWG lines (S=0.3 mm, G=0.15 mm, h=0.2 mm) in an L1 GND pour (zone clearance 0.15 mm) with via fences at 1.6 mm pitch (CPW_A, passes) and 4.8 mm pitch (CPW_B, RF-001).

## Files

- `board.kicad_pcb`, `board.kicad_pro`: the board (KiCad 10 syntax, generated with KiCad 10.0.5).
- `expected.yaml`: ground truth for the readers and analyses (schema: `../expected.schema.json`).
- `drc.json`: `kicad-cli pcb drc --format json --severity-all` output.
- `board.ipc2581.xml`: IPC-2581 rev C export, `board.odb.zip`: ODB++ export, `board.png`: 3D render.

## Deliberate features and their rules

| Rule | Nets | Layer | Location (mm, file frame) | Created by |
|---|---|---|---|---|
| RF-001 | CPW_B | L1 | 30.000, 30.000 | `boardgen.features.cpwg` |

## KiCad DRC

0 errors, 0 warnings (`drc.json`). By type: `{}`.

## Regenerate

```
python tools/boardgen/build_all.py --only cpwg
```

Licence: CC-BY-4.0 (see the `.license` sidecars).
