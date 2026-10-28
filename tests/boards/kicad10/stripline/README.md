# stripline

6-layer 1.6 mm board. SL_SYM: symmetric stripline on L3 between L2 and L4 GND (0.2 mm each side). SL_OFF: offset stripline on L4 at y=30 mm where L3 is empty above and L5 GND is 0.2 mm below (L4 plane exists only for y<20 mm).

## Files

- `board.kicad_pcb`, `board.kicad_pro`: the board (KiCad 10 syntax, generated with KiCad 10.0.5).
- `expected.yaml`: ground truth for the readers and analyses (schema: `../expected.schema.json`).
- `drc.json`: `kicad-cli pcb drc --format json --severity-all` output.
- `board.ipc2581.xml`: IPC-2581 rev C export, `board.odb.zip`: ODB++ export, `board.png`: 3D render.

## Deliberate features and their rules

None: this board is a clean reference (`must_not_report` lists every catalogued rule).

## KiCad DRC

0 errors, 0 warnings (`drc.json`). By type: `{}`.

## Regenerate

```
python tools/boardgen/build_all.py --only stripline
```

Licence: CC-BY-4.0 (see the `.license` sidecars).
