# diff_pair

6-layer board. One 100 ohm class edge-coupled pair DP_P/DP_N: L1 microstrip (3 mm uncoupled breakout, 0.4 mm length mismatch bump), a blind via pair with 4 GND return vias, L3 stripline with a gap step, a second via pair with 4 return vias, and L1 pads.

## Files

- `board.kicad_pcb`, `board.kicad_pro`: the board (KiCad 10 syntax, generated with KiCad 10.0.5).
- `expected.yaml`: ground truth for the readers and analyses (schema: `../expected.schema.json`).
- `drc.json`: `kicad-cli pcb drc --format json --severity-all` output.
- `board.ipc2581.xml`: IPC-2581 rev C export, `board.odb.zip`: ODB++ export, `board.png`: 3D render.

## Deliberate features and their rules

| Rule | Nets | Layer | Location (mm, file frame) | Created by |
|---|---|---|---|---|
| DP-001 | DP_P, DP_N | L1 | 11.500, 15.000 | `boardgen.features.diff_pair` |
| DP-002 | DP_P, DP_N | L1 | - | `boardgen.features.diff_pair` |
| DP-003 | DP_P, DP_N | L3 | 72.500, 15.000 | `boardgen.features.diff_pair` |

## KiCad DRC

0 errors, 0 warnings (`drc.json`). By type: `{}`.

## Regenerate

```
python tools/boardgen/build_all.py --only diff_pair
```

Licence: CC-BY-4.0 (see the `.license` sidecars).
