# ADR-0002: Python uv-workspace core, FastAPI + CLI, local React web UI, Rust only on evidence

- Status: Accepted (2026-10-27)

## Context
- The heavy numerics already live in compiled libraries: GEOS (shapely 2), gmsh, SuiteSparse/MKL, and the external solvers.
- Contributors are mostly SI/RF engineers who write Python.
- We need a headless path for CI, agents and the KiCad plugin.
- Rendering 10^5-10^6 board polygons needs WebGL-class performance. Research 06 §4 compared a Qt desktop, a local web UI and a Rust core.

## Decision
- **Core:** Python ≥ 3.11 as a **uv workspace monorepo** (`packages/*`), one distribution per layer/package (interfaces.md §0). Layering and analysis independence are enforced by **import-linter** in CI.
- **Interfaces:** a typed Python API, the `oa` CLI (typer), and a **FastAPI** local server (REST + WebSocket, 127.0.0.1 with a bearer token). The OpenAPI schema is committed and TS clients are generated from it.
- **UI:** a local web app (React 18 + TypeScript + Vite):
  - **deck.gl** for the 2D board;
  - **three.js** for 3D;
  - **Plotly** for plots;
  - RJSF for schema-driven forms.

  The build is static, served by `oa serve`, and also used for HTML reports. The core is UI-agnostic. A Qt client stays possible because the UI is only an API client.
- **Native code:** no Rust or C++ in M0-M2. Rust via PyO3/maturin (an optional `oa-native` crate) is admitted only when WP-304 profiling shows a hot spot more than 3× over budget. The likely candidates are the S-expression parser and the BEM kernels. Numba is allowed earlier, as an optional speed-up with a pure-numpy fallback.

## Consequences
- There are two toolchains (uv and pnpm). The mock server (MSW) plus the committed schemas let the web and Python work proceed in parallel.
- Large arrays travel as Arrow IPC, npy or GLB, never as JSON.
- Packaging ships wheels with the bundled `web/dist`. A desktop shell (pywebview or Tauri) can come later without architectural change.
