# oa-accel

GPU acceleration core (ADR-0009, WP-503): device selection, GPU memory budget, one CuPy backend
behind a NumPy/SciPy CPU oracle. `import oa_accel` is lazy (no NumPy, SciPy or CuPy import). This
is the only package that loads `cupy`.

Install: `uv sync` gives the CPU path only. GPU: `oa-accel[cuda12]` (CuPy + nvmath-python/cuDSS),
`[cuda13]`, or `[rocm]` (AMD `amd-cupy`; **untested on hardware**, best effort).

## Public API
- `select(policy) -> Backend`. `policy` is a `DevicePolicy(device, config_device, fallback, mem_gb,
  problem, thresholds)` or a device string. Precedence: argument, config, env `OA_DEVICE`
  (`auto|cpu|cuda[:N]|rocm[:N]`), `auto`. `auto` needs a supported GPU (CC >= 7.0; AMD allowlist),
  a `Problem` above the crossover (dense n >= 1500, sparse nnz >= 2e6, FDTD cells >= 2e5) and a
  memory estimate inside the budget. `decide()` is the pure, testable core.
- `Backend`: `dense_solve(a, b, assume="gen"|"sym"|"pos")`, `sparse_solve(a, b, method="auto"|
  "direct"|"cg"|"gmres", m=None|"jacobi"|op, tol, assume="gen"|"spd")`, `sparse_matvec`,
  `raster_stencil(u, weights)` (matrix-free 5-/7-point), `to_device/to_host`, `memory_info`,
  `report() -> AccelReport`. Host arrays in and out; `host=False` keeps results on the device.
  `fallback="cpu"` retries on the CPU after a device error or OOM and records it in
  `report().fallbacks`; `fallback="error"` raises `AccelError(ErrorInfo{code="ACCEL_*"})`.
  Input problems (`ACCEL_SINGULAR`, `ACCEL_NOT_CONVERGED`, `ACCEL_BAD_INPUT`) are never retried.
- `MemoryBudget` (`OA_GPU_MEM_GB`, default `min(0.75 x total, total - 2 GiB)`; `OA_GPU_PINNED_GB`,
  default 2): applied as the CuPy pool limit; `estimate` helpers `dense_solve_bytes`,
  `sparse_solve_bytes`, `stencil_bytes`; over-budget work is refused with `ACCEL_OUT_OF_MEMORY`
  before any allocation.
- `compare(gpu, cpu, cls)` and `TOLERANCES`: ADR-0009 section 5 classes (dense 1e-10 rel; sparse
  direct 1e-9 abs; iterative 1e-7 rel; stencil 1e-12; FDTD 1e-4 abs). Same device and versions is
  bitwise identical; GPU vs CPU is by tolerance only.
- `python -m oa_accel.doctor [--json]` (`oa-accel-doctor`): capability report (`capabilities()`).
  The `oa doctor` integration belongs to `oa-cli`.

## Solvers (WP-504, `oa_accel.solvers`)
- `batched_dense_solve(backend, [(A, b), ...])`: groups by shape; n <= 512 groups are stacked into
  one batched cuSOLVER call, larger ones go through `Backend.dense_solve` (memory check, CPU
  fallback). Returns host arrays in input order.
- `spd_solve(backend, A, b, method="auto"|"direct"|"cg", tol)` -> `(x, label)`: cuDSS Cholesky, or
  CG + Jacobi (the portable ROCm path), or SciPy on the CPU. Used by `oa_pi.dc.solvers.solve_spd`.
- `cg_stencil(backend, weights, rhs)`: matrix-free Jacobi-CG on a uniform 2D/3D grid.
- `THRESHOLDS`: measured crossovers (dense n 600, sparse nnz 1e6); see
  `docs/change-requests/WP-504-thresholds-and-hooks.md`.

## Contracts and gaps
- `ErrorInfo` is a local four-field copy (layer reasons): `docs/change-requests/WP-503-errorinfo-layer.md`.
  Root config (layer, cupy-import contract, marker): `WP-503-root-config.md`.
- Pinned-memory limit is recorded but not enforced (CuPy has no limit API). The GPU lease
  (`OA_GPU_SLOTS`) is `oa_jobs`' job. cuDSS is labelled `deterministic=False`; no nvmath
  multithreading library is configured, so cuDSS planning is single-threaded.
- ROCm: no sparse direct (use CG/GMRES), code and fake-device tests only.

## Tests and golden suites
`packages/oa-accel/tests`: CPU tests always run; tests marked `gpu` (and the `cuda` parametrization
of `test_backends.py`) need `OA_RUN_GPU=1` and a CuPy device, otherwise they skip. No golden suites.
Micro-benchmark: `OA_RUN_GPU=1 python packages/oa-accel/bench/micro.py [--json]`.
