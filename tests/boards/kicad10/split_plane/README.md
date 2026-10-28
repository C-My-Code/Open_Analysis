# split_plane

4-layer board with deliberate reference-plane defects under L1 traces: 1 mm slot (RP-001), trace 0.1 mm from the plane edge (ED-001), trace 0.3 mm from the board edge (ED-002), trace over a void (RP-007), swiss-cheese antipad cluster (RP-002) and a GND -> +3V3 reference change (RP-003).

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

## KiCad DRC

0 errors, 25 warnings (`drc.json`). By type: `{"warning": {"via_dangling": 25}}`.

## Regenerate

```
python tools/boardgen/build_all.py --only split_plane
```

Licence: CC-BY-4.0 (see the `.license` sidecars).
