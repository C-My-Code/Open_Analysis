<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->
# Ingest cross-validation (WP-208)

Compares the native KiCad reader, the IPC-2581 reader and `expected.yaml` on the WP-102 boards and on three KiCad demos.

```
uv run pytest tests/integration/ingest            # 87 tests, ~20 s; `kicad` tests skip without KiCad
uv run python tests/integration/ingest/ingest_report.py --synthetic --text
uv run python tests/integration/ingest/ingest_report.py board.kicad_pcb --json - --csv diff.csv
```

| File | Purpose |
|---|---|
| `ingest_xval.py` | The harness: `compare_cross`, `compare_expected`, `Finding`, the exception judge, `canonical_board`, `fill_outside_outline`. |
| `ingest_report.py` | The report tool (JSON schema `oa-ingest-report/1`, text, CSV). Exit 0 / 1 (failing finding) / 2 (usage or read error). |
| `test_cross_synthetic.py` | WP-102 boards: expected.yaml (native 0.1 % area, 1 nm z), native vs IPC (0.5 % area, 1 um z), `.oab` round trip, v9 vs v10, fault injection. |
| `test_cross_demos.py` | `pic_programmer`, `stickhub`, `cm5_minima` via `kicad-cli pcb export ipc2581` (marker `kicad`). |
| `test_backdrill_conventions.py` | KiCad reader, IPC reader, `oa_geometry.via_stub` and pcbnew agree that the listed end layer is drilled. |
| `test_stale_fill.py` | Emptied, edited-track and edited-outline zones, then `refill_zones` (marker `kicad`). |
| `test_report_cli.py` | The report tool. |
| `pcbnew_backdrill_probe.py` | Runs under KiCad's Python only. |

Known differences live in `tests/golden/ingest_cross/exceptions.toml`; each entry carries a cause and evidence. A difference that matches no entry is a failure. Pinned counts: `tests/golden/ingest_cross/{synthetic_matrix,demo_matrix}` (`--update-golden`). Findings: `docs/change-requests/WP-208-ingest-cross-findings.md`.
