<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->
# WP-504: measured crossovers, ownership and hook requests

Reference machine: RTX 4080 SUPER (16 GB, fp64 1/64 rate), Windows 11, CuPy 14.2, cuDSS via
nvmath 1.0. Numbers come from `cm5_minima` sections (402 unique, median 1335 panels) solved
through `solve_bem` (static, one thread) and from the `power_plane` DC board.

## 1. Thresholds (`oa_accel.policy.Thresholds`, owned by WP-503)

`oa_accel.solvers.THRESHOLDS` carries the measured defaults; `policy.py` is not ours.

| Crossover | ADR-0009 placeholder | Measured | Evidence |
|---|---|---|---|
| dense n (BEM, assembly + LU on device) | 1500 | 600 | n 600-1000: CPU 0.086 s, GPU 0.035 s; n 300-600: GPU slower (first-call cost) |
| sparse nnz (DC IR) | 2e6 | 1e6 | 2D Laplacian: 1e5 unknowns (5e5 nnz) cuDSS 0.23 s vs SuperLU 0.22 s (parity); 1e6 unknowns 2.5 s vs 6.0 s. `power_plane` FEM: 655k unknowns solve 4.0 s vs 7.7 s; 992k unknowns 3.9 s vs 8.8 s |

Proposed change: set `Thresholds.dense_n = 600`, `Thresholds.sparse_nnz = 1_000_000`.

## 2. Files outside the owned paths that this WP had to edit

- `packages/oa-impedance/src/oa_impedance/bem/iterative.py` (WP-512): the additive-Schwarz
  preconditioner the task asked for lives there.
- `packages/oa-impedance/pyproject.toml`, `packages/oa-pi/pyproject.toml`: the `oa-accel`
  dependency (allowed by the dependency rule, listed for the integrator).

## 3. Root import-linter contract (WP-000 / integrator)

`lint-imports` reports "Only oa_accel and oa_fdtd.engine import cupy" BROKEN as soon as any L4
package imports `oa_accel`, through the chain `oa_impedance -> oa_accel.solvers ->
oa_accel.policy -> oa_accel.backend -> oa_accel.ops -> nvmath`. The contract needs
`allow_indirect_imports = true` (as the `oa_model` and `oa_service` contracts have), or an
`ignore_imports = ["oa_accel.* -> nvmath", "oa_accel.* -> cupy", "oa_accel.* -> cupyx"]`. The
direct rule (no L4 package imports cupy itself) holds. Root `pyproject.toml` is not ours; every
other contract is KEPT.

## 4. Hook requests

- `oa_impedance/service.py` (`solve_many`): unchanged, so batching is by threads and the device
  gate in `bem/solver.py`; a service-level grouping by panel count would need that file.
- `oa_pi/dc/config.py`: `MeshConfig.solver` has no `"gpu"` value; the GPU is chosen by the
  device policy (`OA_DEVICE`, `auto` above the sparse crossover) when `solver == "auto"`.
- `oa_pi.dc.fem` meshing dominates B-DC-02 (39 s of 51 s at 6.5e5 unknowns); not in this WP.
