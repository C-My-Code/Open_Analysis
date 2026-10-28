# power_strip

Analytic DC resistance variant: a single 50 x 5 mm L1 copper strip with full-width end pads J1/J2 on a 4-layer board; R = Rs L / W = 4.926 mOhm.

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
python tools/boardgen/build_all.py --only power_strip
```

Licence: CC-BY-4.0 (see the `.license` sidecars).
