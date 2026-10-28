# power_two_layer_vias

Analytic DC variant: two 20 x 20 mm squares (L1 and L4, net VX) joined by 9 vias (3 x 3, 2 mm pitch); 6 x 6 mm terminal pads J1 (L1) and J2 (L4) over the array.

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
python tools/boardgen/build_all.py --only power_two_layer_vias
```

Licence: CC-BY-4.0 (see the `.license` sidecars).
