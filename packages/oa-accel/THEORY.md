# oa-accel: theory, error budget and validation

GPU acceleration layer: device selection, a GPU memory budget, and one CuPy backend behind a NumPy/SciPy CPU oracle. Code: `packages/oa-accel/src/oa_accel/` (`policy.py`, `backend.py`, `ops.py`, `tolerance.py`, `memory.py`). It is the only package that imports CuPy. See the [theory index](../../docs/theory/README.md); design in [ADR-0009](../../docs/adr/0009-gpu-acceleration.md).

This package computes **no physics**. Its job is to guarantee that moving a computation to the GPU does not change the answer by more than a stated, tiny amount. The page therefore states the equivalence rules and what has actually been tested.

## 1. Problem statement

Given a dense solve $Ax=b$, a sparse solve (direct or iterative), a matrix-free stencil apply, or an FDTD run, return a result from the GPU that agrees with the CPU reference within a class-specific tolerance, or fall back to the CPU (or raise) otherwise. Inputs and outputs are host arrays [any unit]; `host=False` keeps results on the device.

## 2. Governing rules and formulation

### 2.1 Device selection (`policy.py`, `decide`)

Precedence: explicit argument, config `accel.device`, environment `OA_DEVICE` (`auto|cpu|cuda[:N]|rocm[:N]`), then `auto`. `auto` selects a GPU only if all hold: a supported device (compute capability at least 7.0; AMD allowlist), the problem is above the crossover, and the memory estimate fits the budget. Crossovers (initial ADR-0009 defaults, to be replaced by measured values, WP-504): dense order $n\ge1500$, sparse nnz $\ge2\times10^6$, FDTD cells $\ge2\times10^5$.

### 2.2 Memory budget (`memory.py`)

`OA_GPU_MEM_GB`, default $\min(0.75\,V_\text{total},\ V_\text{total}-2\ \text{GiB})$, applied as the CuPy pool limit. Estimators (`dense_solve_bytes`, `sparse_solve_bytes`, `stencil_bytes`) refuse over-budget work with `ACCEL_OUT_OF_MEMORY` before any allocation. The pinned-memory limit is recorded but not enforced.

### 2.3 Operations (`ops.py`)

| Operation | CPU oracle | GPU |
|---|---|---|
| Dense solve (`assume` = `gen`, `sym` or `pos`) | `scipy.linalg.solve` with the matching `assume_a` | LU (`cp.linalg.solve`, cuSOLVER getrf/getrs) for `gen` and `sym`; Cholesky (`potrf`) plus triangular solves for `pos` |
| Sparse direct | SciPy SuperLU | cuDSS via nvmath-python (labelled `deterministic=False`) |
| Sparse iterative | `scipy.sparse.linalg.cg` / `gmres`, optional Jacobi preconditioner | `cupyx.scipy.sparse.linalg.cg` / `gmres`, optional Jacobi |
| Raster stencil | 5-/7-point matrix-free apply | same stencil on device |

The Jacobi preconditioner is the inverse diagonal. Input problems (`ACCEL_SINGULAR`, `ACCEL_NOT_CONVERGED`, `ACCEL_BAD_INPUT`) are never retried on another device; device errors and out-of-memory events are retried on the CPU when `fallback="cpu"` (recorded in `AccelReport.fallbacks`) and raised as `AccelError` when `fallback="error"`.

### 2.4 Numerical equivalence rules (`tolerance.py`, ADR-0009 section 5)

- **Same device, same library versions, same input: bitwise identical.** Achieved by avoiding floating-point `atomicAdd` and using fixed-order reductions.
- **GPU against CPU: tolerance only**, never bitwise. `compare(gpu, cpu, cls, scale)` returns max absolute difference $\max|a-b|$ and relative difference $\max|a-b|/\max|b|$ and passes if the absolute limit (tolerance times `scale`) or the relative limit is met.

| Class | Relative limit | Absolute limit |
|---|---|---|
| `dense` (BEM, float64) | $10^{-10}$ | - |
| `sparse_direct` (DC IR) | - | $10^{-9}\times$ `scale` (pass `scale = V_set`); KCL residual $\le10^{-9}$ |
| `sparse_iterative` | $10^{-7}$ | - (both backends converge to $\lVert r\rVert/\lVert b\rVert\le10^{-10}$) |
| `stencil` | $10^{-12}$ | - |
| `fdtd` | - | $10^{-4}$ in $|\Delta S|$ (about $-80$ dB) |

Why these numbers are plausible: two correct solvers of a well-conditioned linear system differ by about $\kappa(A)\,\epsilon_\text{machine}$ relative; with $\epsilon\approx10^{-16}$ in float64, $10^{-10}$ allows $\kappa$ up to about $10^6$. ADR-0009 states this condition (condition number $\le10^6$ on the B-IMP cases). For a case with a worse condition number, a GPU/CPU difference above $10^{-10}$ would not indicate a bug, and the tolerance should be argued from $\kappa$, not relaxed silently.

## 3. Assumptions and validity range

- The equivalence rules assume **float64 on both sides** for dense and sparse solves. Float32 on the GPU is used only in FDTD, where the accumulators (probe sums, DFT) are float64 and the gate is $10^{-4}$ in S.
- The dense tolerance assumes $\kappa(A)\lesssim10^6$ (see above). Ill-conditioned systems may legitimately exceed it.
- Iterative solvers are only equivalent when both converge to the same residual tolerance; stagnation or different iteration counts are reported as `ACCEL_NOT_CONVERGED`.
- **cuDSS is not deterministic** by label (`deterministic=False`); repeat GPU runs of the sparse direct path may differ in the last bits, within the $10^{-9}$ gate. Multithreading in nvmath is not configured, so cuDSS planning is single-threaded.
- **The `residual` field of `Tolerance`** (required relative residual on both backends) is not checked inside `compare()`. It is checked in the tests by calling `relative_residual` separately (`test_backends.py` asserts a relative residual below $10^{-10}$ in most tests and below $10^{-9}$ in one). A user of `compare()` outside those tests must check the residual themselves.
- The ADR's additional FDTD criterion, derived $Z_0$ within 0.01 %, is not in `TOLERANCES`.
- ROCm is untested on hardware (no AMD GPU); there is no sparse direct solve on ROCm from Python (CG/GMRES only).
- Crossover thresholds are initial values, not measured ones (WP-504).

## 4. Error sources and magnitudes

| Quantity | Bound | Source |
|---|---|---|
| Dense GPU against CPU | $10^{-10}$ relative | `TOLERANCES["dense"]` |
| Sparse direct (DC IR node voltages) | $10^{-9}\,V_{set}$ | `TOLERANCES["sparse_direct"]` |
| Sparse iterative | $10^{-7}$ relative | `TOLERANCES["sparse_iterative"]` |
| Stencil | $10^{-12}$ relative | `TOLERANCES["stencil"]` |
| FDTD float32 against float64 | $|\Delta S|\le10^{-4}$ | `TOLERANCES["fdtd"]`, measured in oa-fdtd |
| Measured, FDTD (oa-fdtd README) | float32 GPU within $10^{-4}$ of the float64 probe DFTs; float64 GPU within $10^{-10}$ of the NumPy engine; same-device runs bitwise equal; CUDA-graph replay equals eager launches | `packages/oa-fdtd/README.md` verification table |

These are tolerances (what counts as equivalent), not measured error distributions, except for the FDTD line.

## 5. Validation evidence

- **FDTD**: the equivalence is measured on both engines in `packages/oa-fdtd/tests` (GPU tests marked `gpu`, run with `OA_RUN_GPU=1`); results in the last row above. Throughput measured: 6.0 Gcells/s float32, 3.2 float64 on a 256^3 grid with CPML (RTX 4080 SUPER).
- **Dense, sparse, stencil**: the `cuda` parametrisation of `test_backends.py` in `packages/oa-accel/tests` compares each operation with the CPU oracle; those tests run only with `OA_RUN_GPU=1` and a CuPy device. I did not find recorded GPU results for these classes in `docs/validation/results.json` or `docs/BENCHMARKS.md` [verify by running the GPU tests]. B-IMP-04 (GPU batch BEM at $10^{-10}$) and B-DC-02 (GPU at 1e6 unknowns) are `pending` or `ready` in the benchmark pages.
- No golden suites (`oa-accel/README.md`).

**Known gaps and open issues (FOLLOWUPS WP-503).**

- No CuPy is installed in the development venv, so the GPU tests skip there; installing the `cuda12` or `cuda13` extra runs them. The three GPU extras are declared as conflicting in the root `[tool.uv]`.
- ROCm untested on hardware. Pinned-memory limit not enforced. `ErrorInfo` is a local copy (layer placement).
- The GPU solve path for BEM batches (B-IMP-04) and for DC IR at scale (B-DC-02) is not benchmarked.
- Benchmark harness page for B-PERF-01 still reads "oa-fdtd does not exist", although the README reports a measurement.

## 6. References

- CuPy documentation (https://docs.cupy.dev) and NVIDIA cuDSS documentation for the algorithms behind the GPU operations; formal citations [verify].
- N. J. Higham, *Accuracy and Stability of Numerical Algorithms*, 2nd ed., SIAM, 2002 (backward error and condition number: forward error $\lesssim\kappa\,\epsilon$).
- Project: `docs/adr/0009-gpu-acceleration.md` sections 3 to 5, `docs/BENCHMARKS.md` B-IMP-04, B-DC-02, B-PERF-01; change requests `docs/change-requests/WP-503-*.md`.
