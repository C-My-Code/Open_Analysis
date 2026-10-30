# WP-505 change request: root configuration for `oa-fdtd`

Status: proposed. Owner of the change: WP-000 / the integrator (shared root files).

## Problem
`oa-fdtd` is a new package. The root `pyproject.toml` lists every package in several places, and
ADR-0010 section 10 requires a layer placement and an import contract for `oa_fdtd.engine`. WP-505
may not edit the root file, so `oa_fdtd` currently works only with `PYTHONPATH` (the WP-505 tests
and benchmark were run that way) or after the integrator re-locks.

## Proposed change (root `pyproject.toml`)
1. Add `oa-fdtd` to `[dependency-groups].dev`, `[tool.uv.sources]` (`{ workspace = true }`), ruff
   `src` and `known-first-party` (`oa_fdtd`), mypy `mypy_path` (`packages/oa-fdtd/src`), and
   `[tool.importlinter].root_packages`. Re-lock (the integrator).
2. Layer placement, in the adapter layer next to the other solver adapters:
   ```toml
   "oa_em_openems | oa_em_palace | oa_fdtd",
   ```
   `oa_fdtd` imports `oa_accel` (device selection, memory budget) and NumPy only. It does not import
   `oa_mesh` yet: the engine-level job carries mesh lines. `oa_mesh.rect` is for the WP-507 adapter,
   which sits above the engine (`oa_fdtd.adapter`).
3. New contract, ADR-0010 section 10:
   ```toml
   [[tool.importlinter.contracts]]
   name = "oa_fdtd.engine imports only oa_accel and oa_mesh.rect"
   type = "forbidden"
   source_modules = ["oa_fdtd.engine"]
   forbidden_modules = ["oa_assistant", "oa_cli", "oa_em", "oa_em_openems", "oa_em_palace",
     "oa_emi", "oa_erc", "oa_framework", "oa_geometry", "oa_impedance", "oa_ingest_ipc2581",
     "oa_ingest_kicad", "oa_jobs", "oa_kicad_bridge", "oa_mcp", "oa_model", "oa_network",
     "oa_physics", "oa_pi", "oa_qs", "oa_results", "oa_server", "oa_service", "oa_si",
     "oa_thermal", "oa_testkit", "oa_native"]
   ```
   The existing "Only oa_accel and oa_fdtd.engine import cupy" contract needs no change:
   `oa_fdtd.engine` takes the CuPy module from `oa_accel`'s backend (`backend.xp`), so `oa_fdtd`
   never imports `cupy` itself. If the integrator prefers a direct `import cupy` in the engine,
   `oa_fdtd` is simply not in that contract's `source_modules`.
4. mypy: no new override (CuPy objects are typed `Any`).
5. Packaging: `packages/oa-fdtd/pyproject.toml` force-includes `kernels/*.cu` in the wheel.
6. `oa-mesh` gains the pure-NumPy subpackage `oa_mesh.rect` (WP-505 owns it); no dependency change.

## Impact
No behaviour change for other packages. A plain `uv sync` installs no GPU stack; the NumPy
reference engine runs everywhere (`engine="numpy"`), and `engine="auto"` falls back to it when
`oa_accel` selects the CPU.
