<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->
# oa-kicad-bridge

kicad-cli and pcbnew bridge (WP-103, ADR-0006). `pcbnew` is only run in a subprocess with KiCad's own
Python; this package never imports it. It does not import `oa_ingest_kicad` or `oa_framework`.

## Public API
- `KiCadTools.discover()` finds `kicad-cli` and KiCad's Python: explicit args, `KICAD_CLI` / `KICAD_PYTHON`,
  `tools.toml` (`$OA_TOOLS_TOML`, `%APPDATA%\open_analysis`, `~/.config/open_analysis`), `KICAD_ROOT`, `PATH`,
  then platform defaults (every drive's `Program Files\KiCad\<ver>\bin`, `/usr/bin`, the macOS bundle).
- `upgrade(path)` (in place), `drc(path) -> DrcReport`, `export_ipc2581(path, out)`, `export_odb(path, out, check_zones=True)`.
- `import_foreign(path, fmt="auto") -> Path` and `import_foreign_report(...) -> ImportResult` (the kicad-cli JSON
  report becomes `Diagnostic`s: `INGEST.IMPORT_ERROR/WARNING/LAYER_MAPPING`).
- `refill_zones(path, out)` runs `pcbnew_helper.py refill` (ZONE_FILLER) with a timeout (default 600 s; pcbnew can
  hang on a wx assertion dialog). `inspect_zones(path)` reports per-zone fill area.
- `prepare_read(path, options)` honours `ReadOptions.refill_zones` (`never`, `if_unfilled`, `always`) and
  `mark_refilled(board)` sets `fill_state="refilled_by_tool"`.
- `AltiumReader` (entry point `altium`, `.PcbDoc`): import, optional refill, then the `kicad` reader found through
  `importlib.metadata.entry_points("open_analysis.ingest")`; `meta.source_tool="altium"`.

Errors: `ToolMissing` (with hint), `ExternalToolFailed` (return code, log tail), `IngestError`.

## Tests and golden
Unit tests mock the CLI. `kicad`-marked tests use the real install: DRC, upgrade, exports, the refill test
(golden `refill_ecc83`: ZONE_FILLER reproduces the fill saved by the KiCad GUI in the shipped demo within 0.01 %;
measured 0.007 %), and an Altium round trip when `OA_ALTIUM_SAMPLES` points to `.PcbDoc` files (KiCad's
`qa/data/pcbnew/plugins/altium`, downloaded to a scratch directory, not committed).
