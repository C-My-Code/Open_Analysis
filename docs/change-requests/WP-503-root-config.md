# WP-503 change request: root configuration for `oa-accel`

Status: proposed. Owner of the change: WP-000 / the integrator (shared root files).

## Problem
`oa-accel` is a new package. The root `pyproject.toml` lists every package in several places, and
ADR-0009 requires a layer placement and a `cupy` import restriction. WP-503 may not edit the root
file, so `oa-accel` currently works only with `PYTHONPATH` or after the integrator re-locks.

## Proposed change (root `pyproject.toml`)
1. Add `oa-accel` to `[dependency-groups].dev`, `[tool.uv.sources]` (`{ workspace = true }`),
   ruff `src` and `known-first-party` (`oa_accel`), mypy `mypy_path`
   (`packages/oa-accel/src`), and `[tool.importlinter].root_packages`. Re-lock (the integrator).
2. Layer placement, next to `oa_physics`:
   ```toml
   "oa_physics | oa_results | oa_accel",
   "oa_model",
   ```
   `oa_accel` imports only numpy, scipy and (optionally) cupy/nvmath. It must not import
   `oa_model`: `ErrorInfo` lives in `oa_framework`, see `WP-503-errorinfo-layer.md`.
3. Forbidden contract:
   ```toml
   [[tool.importlinter.contracts]]
   name = "Only oa_accel and oa_fdtd.engine import cupy"
   type = "forbidden"
   source_modules = ["oa_assistant", "oa_cli", "oa_em", "oa_em_openems", "oa_em_palace",
     "oa_emi", "oa_erc", "oa_framework", "oa_geometry", "oa_impedance", "oa_ingest_ipc2581",
     "oa_ingest_kicad", "oa_jobs", "oa_kicad_bridge", "oa_mcp", "oa_mesh", "oa_model",
     "oa_network", "oa_physics", "oa_pi", "oa_qs", "oa_results", "oa_server", "oa_service",
     "oa_si", "oa_thermal", "oa_testkit"]
   forbidden_modules = ["cupy", "cupyx", "nvmath"]
   ```
   `oa_fdtd` is added to the exempt list by WP-505 (only `oa_fdtd.engine` may import cupy).
   Note: `oa_accel` itself loads CuPy via `importlib.import_module`, so the contract needs no
   `ignore_imports` for it.
4. mypy: `[[tool.mypy.overrides]] module = ["cupy", "cupyx", "cupyx.*", "nvmath", "nvmath.*"]`
   with `ignore_missing_imports = true` (oa_accel keeps CuPy typed as `Any`).
5. pytest: add the marker `"gpu: needs a GPU and OA_RUN_GPU=1"` to `markers`. Until then
   `packages/oa-accel/tests/conftest.py` registers it itself.

## Impact
No behaviour change for other packages. A plain `uv sync` installs no GPU stack (the `cuda12`,
`cuda13` and `rocm` extras are opt-in).
