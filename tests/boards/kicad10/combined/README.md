# combined

8-layer 2.0 mm board carrying every violation of the other boards in separate regions, plus net classes 50ohm / 90ohm / 100ohm and the USB_D+ / USB_D- pair. L2/L4/L7 GND planes, L5 +1V0 plane (full board).

## Files

- `board.kicad_pcb`, `board.kicad_pro`: the board (KiCad 10 syntax, generated with KiCad 10.0.5).
- `expected.yaml`: ground truth for the readers and analyses (schema: `../expected.schema.json`).
- `drc.json`: `kicad-cli pcb drc --format json --severity-all` output.
- `board.ipc2581.xml`: IPC-2581 rev C export, `board.odb.zip`: ODB++ export, `board.png`: 3D render.

## Deliberate features and their rules

| Rule | Nets | Layer | Location (mm, file frame) | Created by |
|---|---|---|---|---|
| RP-001 | SIG_SLOT | L1 | 50.000, 15.000 | `boardgen.features.split_plane` |
| ED-001 | SIG_PLEDGE | L1 | 30.000, 51.750 | `boardgen.features.split_plane` |
| RP-007 | SIG_NOREF | L1 | 75.000, 56.000 | `boardgen.features.split_plane` |
| RP-002 | SIG_SWISS | L1 | 20.000, 30.000 | `boardgen.features.split_plane` |
| RP-003 | SIG_REFCHG | L1 | 75.000, 35.000 | `boardgen.features.split_plane` |
| ED-002 | SIG_BOARDEDGE | L1 | 0.400, 25.000 | `boardgen.features.split_plane` |
| IM-002 | MS_250 | L1 | 135.000, 10.000 | `boardgen.features.microstrip` |
| IM-002 | MS_500 | L1 | 135.000, 30.000 | `boardgen.features.microstrip` |
| RF-001 | CPW_B | L1 | 195.000, 30.000 | `boardgen.features.cpwg` |
| VI-001 | SE1 | L6 | 15.000, 90.000 | `boardgen.features.via_transition` |
| RP-004 | NR | L1 | 55.000, 110.000 | `boardgen.features.via_transition` |
| DP-001 | DP_P, DP_N | L1 | 81.500, 100.000 | `boardgen.features.diff_pair` |
| DP-002 | DP_P, DP_N | L1 | - | `boardgen.features.diff_pair` |
| DP-003 | DP_P, DP_N | L3 | 142.500, 100.000 | `boardgen.features.diff_pair` |
| PD-001 | +1V0 | L1 | 198.500, 98.500 | `boardgen.features.power_loads` |

## Notes

- KiCad has no explicit diff-pair object: the Altium-style explicit pair is represented by the +/- naming and the 90ohm netclass pattern USB_D*.

## KiCad DRC

0 errors, 28 warnings (`drc.json`). By type: `{"warning": {"via_dangling": 28}}`.

## Regenerate

```
python tools/boardgen/build_all.py --only combined
```

Licence: CC-BY-4.0 (see the `.license` sidecars).
