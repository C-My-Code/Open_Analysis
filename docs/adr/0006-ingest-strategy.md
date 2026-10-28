# ADR-0006: Own KiCad reader primary; IPC-2581 second; Altium via kicad-cli; pcbnew only as an optional subprocess helper

- Status: Accepted (2026-10-27)

## Context
Research 01 found:
- No maintained Python parser covers KiCad 9/10 (kiutils stalled in 2024).
- The SWIG pcbnew API cannot reach the dielectric stackup and is removed in KiCad 11.
- The IPC API needs a running GUI until KiCad 11.
- `kicad-cli pcb import` converts Altium, PADS, Eagle, CADSTAR and others headless.
- IPC-2581 carries the stackup with Dk/Df and is exported by every major EDA tool.

## Decision
1. **Primary:** our own streaming `.kicad_pcb` + `.kicad_pro` reader (`oa-ingest-kicad`). It is version-tolerant from v7 to v10 and handles both the v9 net table and v10 name-only nets.
2. **Second:** an IPC-2581 reader (`oa-ingest-ipc2581`). It also cross-validates reader 1 in CI (WP-208).
3. **Altium and other foreign formats:** `kicad-cli pcb import` as a subprocess, then reader 1 (`oa-kicad-bridge`). There is no native PcbDoc parser until there is demand (M5 at the earliest).
4. **Optional helper:** pcbnew run with KiCad's bundled Python (`D:\Program Files\KiCad\10.0\bin\python.exe` on the reference machine, configurable) as a **subprocess**, for zone refill and test oracles only. It is never imported into our processes.
5. ODB++ comes later (M5). The KiCad IPC live plugin comes in M2 for cross-probing, not as an ingest path.

## Consequences
- We track KiCad format changes ourselves, roughly yearly. The version table and the demo-board corpus in CI mitigate this.
- Stale or unfilled zones are detected and flagged. PI refuses them unless the user refills (via the helper) or overrides.
- When KiCad 11 ships, the helper moves to the headless IPC API without touching the readers.
