<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->
# oa-ingest-ipc2581

IPC-2581 rev B and C reader (WP-103). Entry point `open_analysis.ingest: ipc2581`.

```python
from oa_ingest_ipc2581 import Ipc2581Reader

board = Ipc2581Reader().read(Path("board.xml"))  # meta.source_tool == "ipc2581"
```

Contracts: `Reader` protocol (interfaces.md 3.2), `Board` v1.0 (units in metres, Y up, ids `h:<sha1>` /
`i:<ref>.<pin>`), errors as `IngestError` (`INGEST.IPC2581_XML`, `...LAYERS`, `...STEP`).
Uses `lxml.iterparse`; no `pcbnew` is ever imported.

## What is mapped
Units (MILLIMETER, MICRON, INCH, MIL; per-dictionary), stackup (thickness, Dk, Df, conductivity, material
names, core/prepreg), padstacks (rev C `PadStackDef` and rev B `PadStack` instances), holes and slots,
via spans, backdrill `Spec`s, lines, arcs, polylines, contours with cutouts, nets (`Set net`, `LogicalNet`),
components and pins, bill-of-material values, `Profile` outline (analytic edges kept).

## Fidelity of KiCad 10.0.5 exports (measured)
| Item | State |
|---|---|
| Zones | No zone object: the filled copper arrives as contours per (net, layer). Zone outline is the convex hull of the fill, priority/hatch/clearance rules are lost. `fill_state` is always `filled` (a stale file cannot be detected). |
| Backdrill | Present as `Spec` START_LAYER / MUST_NOT_CUT_LAYER / MAX_STUB_LENGTH. Backdrill diameter and the stub-length value are not kept in the model (see change request). |
| Materials | Dk and Df only; no frequency data, roughness or Dk anisotropy. Solder mask has Dk but no Df. KiCad always writes a 10 um mask on both sides, even when the board defines none (z origin is therefore 10 um below the lowest copper). |
| Via types | No micro-via flag: micro vias read as `blind`. |
| Pads | Paste/mask-only apertures (no copper, no drill) are not pads. Pads with the same number stacked at one place on disjoint layers merge. |
| Layer names | Boards without a stackup export copper as `top_cu`/`bottom_cu`; the model ids stay `L1..Ln`. |
| Text and graphics on copper | Strokes become `CopperShape`s (counted in copper area). |
| Net classes, diff pairs, courtyards | Not in the file. Diff pairs come from the naming heuristic. |

## Golden suites
`tests/golden/ingest_ipc2581/{ecc83_pp,stickhub,cm5_minima,pic_programmer}`: counts exact, per-layer and
per-net copper area (<= 0.5 %), stackup thickness/Dk/Df, recorded from pcbnew by
`tests/record_golden.py` (runs `tests/oracle_pcbnew.py` in KiCad's Python). `refill_ecc83` belongs to the bridge.
Tests that export from KiCad carry the `kicad` marker. The WP-102 boards under `tests/boards/kicad10` are
cross-checked against their `expected.yaml` (counts, stackup z, copper area, backdrill).

## Non-KiCad files
Set `OA_IPC2581_SAMPLES` to a directory with `.cvg`/`.xml` files. The Altium-produced rev B samples
`HMI BOARD.cvg` and `SWITCH BOARD.cvg` from github.com/mgburr/ipc2581-to-kicad (`samples/`, AGPL-3.0 repository,
so not copied here) parse without errors; the IPC-2581 benchmark of the IEEE EPS TC-EDMS needs a manual download.
`tests/data/mini_revb.xml` is a small hand-written rev B file in the same dialect and is always tested.
