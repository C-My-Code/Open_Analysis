# oa-fdtd

Yee-scheme FDTD for PCB regions (ADR-0010, WP-505 core engine): non-uniform graded grid, fused CUDA
kernels through CuPy, and a NumPy reference engine that is the test oracle and CPU fallback.
WP-506 adds SIBC copper, conformal PEC, ADE dielectrics and mode ports; WP-507 the adapter.

## Public API
- `oa_fdtd.grid.Grid(x, y, z)`: mesh lines [m]; storage layout (SoA, `(B, z+2, y+2, pitch)`, x
  fastest, rows padded to 32), Courant `dt`, inverse primal/dual lengths. `oa_mesh.rect` builds the
  lines (layers, copper-face thirds rule, grading).
- `oa_fdtd.model`: `Model` (grid, `Waveform`, `Region`, `LumpedPort`, `SoftSource`, `Probe`, walls
  `pec|pmc|cpml` per face, `CpmlSpec`, `drive` batch matrix) and `compile_model`, which builds the
  `uint16` averaged-material edge index (0 = PEC) and the `ca/cb` table.
- `oa_fdtd.run.run(model, engine="auto|numpy|cuda", dtype, end_db, ...) -> RunResult`; `sparams()`
  gives power-wave S from the lumped-port voltages (`S = B A^-1` with all N excitations as a batch).
  `engine="auto"` uses `oa_accel.select` (crossover 2e5 cells, memory budget via
  `MemoryBudget.require`, `AccelReport` in `RunResult.report`).
- `oa_fdtd.engine.CupyEngine` / `oa_fdtd.reference.NumpyEngine`: same equations, same arrays.
  `oa_fdtd.engine.estimate_bytes` is the memory estimator.
- `oa-fdtd run job.json` (`oa_fdtd.cli`): engine-level job; no `oa_framework` import.

## Models implemented
Fused E and H kernels (one thread per cell), float32 or float64; uniform-material z groups drop the
index read (PEC edges in such groups are zeroed by a list kernel); a CUDA graph replays 64 steps;
CPML (CFS, m=3, kappa_max 8, alpha from `f_low`) with psi only in the slabs; PEC and PMC (mirror
ghost layer) walls; modulated Gaussian (no DC) or Gaussian (DC) excitation; resistive lumped
ports; float64 running DFT on the device; deterministic fixed-order probe reductions; energy end
criterion; multi-excitation batching as the leading array dimension.

## Verification (ADR-0010 section 9, both engines; GPU tests need `OA_RUN_GPU=1`)
| Case | Result |
|---|---|
| 1 PEC cavity TE101/TE011/TM110, uniform and graded, Richardson over 3 grids | error < 0.05 %, observed order 2.0 (NumPy f64; CUDA f32) |
| 2 CPML, 10 cells, 0.1-40 GHz, both faces | worst -84 dB (normal incidence); late-time energy < -80 dB |
| 3 parallel-plate TEM line, lumped ports R = eta h / w, PMC sides | S11 < -60 dB, abs(abs(S21) - 1) < 1e-4 up to 3 GHz at 0.5 mm cells |
| 7 closed-box energy, 1e5 steps | float64 < 1e-6 (NumPy and CUDA); float32 CUDA 3.3e-7 |
| Yee dispersion vs the exact relation | within 1e-3 (f64) |
| GPU vs NumPy | f32 within 1e-4 of the f64 probe DFTs; f64 GPU within 1e-10; same-device runs bitwise equal, graph equals eager |

B-PERF-01 plain kernel (256^3 vacuum, 10-cell CPML on all faces, RTX 4080 SUPER, 192 steps,
`bench/throughput.py`): 6.0 Gcells/s float32 (target >= 3.0), 3.2 float64; NumPy float64
0.014 Gcells/s. The PCB-like case (ADE) belongs to WP-506.

## Known gaps
- Above about 3 GHz at 0.5 mm cells the lumped-port |S21| deviates from 1 by O(f^2) (3.8e-3 at
  20 GHz); the cause is not yet separated between port discretization and the source spectrum.
- CPML reflection is measured at normal incidence only; oblique and evanescent cases are open.
- Material sets above 65535 averaged entries are refused; material averaging is host-side float64
  and intended for regions up to a few 1e6 cells (the vacuum fast path has no limit).
- ROCm: same `.cu` source, hiprtc; written but untested on hardware (no AMD GPU). Graph capture
  falls back to eager launches if the runtime refuses it.
- Magnetic materials, anisotropy, Mur ABC, field dumps and subgridding are not implemented.
- Tests import `oa_fdtd` through the workspace; until the root config lands
  (`docs/change-requests/WP-505-root-config.md`) run them with `PYTHONPATH=packages/oa-fdtd/src`.

## NF2FF compatibility
Probes are index lists with weights, summed on the device in float64 and accumulated into a
running DFT per requested frequency. A Huygens surface is the same machinery without the final sum:
the tangential E and H points of a box (`Grid.points`) as one probe each, DFT'd per point on the GPU,
so WP-506 can add the near-to-far-field transform without changing the kernels or the layout.

## Tests and golden suites
`packages/oa-fdtd/tests` (CPU always; `gpu` marker with `OA_RUN_GPU=1`; the graded NumPy cavity case is
`slow`). Golden suites `tests/golden/fdtd/{cavity,cpml,tem_line}` (analytic cases).
