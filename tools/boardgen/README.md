<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# boardgen: synthetic KiCad test board generator (WP-102)

Writes the boards in `tests/boards/kicad10/` and their ground truth. Licence of this code: GPL-3.0-or-later. The
generated boards are CC-BY-4.0.

```
python tools/boardgen/build_all.py [--out DIR] [--only NAME ...] [--no-render]
python tools/boardgen/validate_expected.py        # pip install pyyaml jsonschema
```

Requirements: KiCad 10.0.5 (`D:\Program Files\KiCad\10.0` or `OA_KICAD_DIR`) and any Python >= 3.9. The generator itself
uses only the standard library; `kicad_post.py` runs inside KiCad's bundled Python (pcbnew, shapely).

| Module | Role |
|---|---|
| `kwriter.py` | Deterministic `.kicad_pcb` / `.kicad_pro` text writer (KiCad 10 and 9 syntax) incl. the stackup (SWIG cannot set it) |
| `features.py` | Feature builders (microstrip, stripline, diff pair, CPWG, split-plane defects, via structures, power loads); each records ground truth and names itself in the `erc` entries |
| `boards.py` | The eleven board definitions |
| `kicad_post.py` | Out-of-process pcbnew step: refill zones (`ZONE_FILLER`), save, count objects, compute copper area per layer/net |
| `build_all.py` | Orchestration: DRC, IPC-2581 rev C, ODB++ zip, render, `expected.yaml`, README, licence sidecars |
| `yamlout.py` | Stdlib YAML emitter (floats keep a `.`, so PyYAML reads them as floats) |
| `validate_expected.py` | JSON Schema + consistency check (`../../tests/boards/kicad10/expected.schema.json`) |

Pipeline per board: `kwriter` text, then pcbnew refill and save (this is the committed `board.kicad_pcb`), then
`kicad-cli pcb drc|export ipc2581|export odb|render`. `microstrip_v9` is written in KiCad 9 syntax and the fills of the
pcbnew-refilled twin are injected, so the copper is identical.

Adding a board: write a function in `boards.py` using `features.py` helpers, register it in `BOARDS`, run `build_all.py
--only NAME`. Keep each feature's `erc`/`negative_cases` entries next to the code that draws it.
