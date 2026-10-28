# power_plane

4-layer board with a thin L2-L3 core (0.1 mm). L2 GND, L3 +1V0 plane 40 x 30 mm, L4 GND. VRM U5 (3 output pins) at the left, load U1 (16 +1V0 + 16 GND pins on a 1 mm grid, via-in-pad) at the right, decap C1 1.5 mm and C2 12 mm from the nearest U1 power pin.

## Files

- `board.kicad_pcb`, `board.kicad_pro`: the board (KiCad 10 syntax, generated with KiCad 10.0.5).
- `expected.yaml`: ground truth for the readers and analyses (schema: `../expected.schema.json`).
- `drc.json`: `kicad-cli pcb drc --format json --severity-all` output.
- `board.ipc2581.xml`: IPC-2581 rev C export, `board.odb.zip`: ODB++ export, `board.png`: 3D render.

## Deliberate features and their rules

| Rule | Nets | Layer | Location (mm, file frame) | Created by |
|---|---|---|---|---|
| PD-001 | +1V0 | L1 | 28.500, 23.500 | `boardgen.features.power_loads` |

## KiCad DRC

0 errors, 0 warnings (`drc.json`). By type: `{}`.

## Regenerate

```
python tools/boardgen/build_all.py --only power_plane
```

Licence: CC-BY-4.0 (see the `.license` sidecars).
