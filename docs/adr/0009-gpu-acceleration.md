# ADR-0009: GPU acceleration through one CuPy backend (`oa-accel`) with a mandatory CPU fallback

- Status: Accepted (2026-10-29)
- Deciders: project owner (direction: "use the GPU the user has, NVIDIA or AMD"), architecture owner
- Supersedes: the WP-413 sketch row in TASKS.md (now WP-503/WP-504, milestone M1P)
- Related: ADR-0002 (stack), ADR-0004 (solvers as jobs), ADR-0008 (compiled core), ADR-0010 (own GPU FDTD engine), [BENCHMARKS.md](../BENCHMARKS.md)

## Context
- M1P ([ROADMAP](../ROADMAP.md)) gates the M1 release on runtime targets in [BENCHMARKS.md](../BENCHMARKS.md). Several of them are out of reach on CPU under our caps (TASKS rule 10: 2 threads per numeric library, 8 GB per test run): 3D FDTD (B-EM-*), 1e6-unknown DC IR (B-DC-03), batched BEM sweeps (B-IMP-04).
- Measured today on the reference machine (Ryzen 9 9950X3D, 47 GB, RTX 4080 SUPER 16 GB, CUDA compute capability 8.9, driver 621, CUDA visible inside WSL2): BEM 16-39 ms per section; DC IR 3e5 unknowns in 10.5 s; openEMS microstrip thru 31 s; Palace p1 microstrip 169 s (CPU, 6 GB box).
- Users have NVIDIA or AMD GPUs, or none. Windows is the majority platform (research 05 §6.1).
- The machine hard-froze several times under uncapped load. Any GPU path must stay inside explicit memory and CPU budgets, and GPU memory exhaustion must never take the host down.

## Decision

### 1. One array/kernel layer: CuPy, behind `oa-accel`
- New package **`oa-accel`** (`oa_accel`, layer of `oa_physics`; imports only numpy, scipy, optional cupy/nvmath and `oa_model` for `ErrorInfo`). It is the only package allowed to `import cupy`; an import-linter `forbidden` contract enforces this. `oa_fdtd.engine` gets a named exception for its kernels (ADR-0010) but still obtains device, stream and memory pool from `oa_accel`.
- **Single source.** Numerical code is written against `xp = backend.xp` (NumPy or CuPy, same API surface), and custom kernels are CuPy `ElementwiseKernel`/`RawKernel` in CUDA C++ restricted to the subset HIP accepts (no inline PTX, no warp intrinsics without `__HIP_PLATFORM_AMD__` guards, warp size taken from the device, never hard-coded 32).
- **Wheels.** NVIDIA: `cupy-cuda12x` (or `cupy-cuda13x`), published for Linux and Windows, x86_64 and aarch64 ([CuPy install guide](https://docs.cupy.dev/en/stable/install.html)). AMD: CuPy's ROCm support is labelled *experimental*, and CuPy stopped publishing ROCm wheels at v13.4.0; AMD currently hosts ROCm 6.4 wheels as `amd-cupy` (same page). We therefore treat ROCm as a **best-effort Linux/WSL tier** with its own CI lane, not as a parity platform.
- Extras: `oa-accel[cuda12]`, `oa-accel[cuda13]`, `oa-accel[rocm]` (documented install from AMD's index), never a hard dependency of any other package. A plain `uv sync` installs no GPU stack.
- **CPU fallback always works.** Every accelerated entry point has a NumPy/SciPy implementation that is the test oracle. CI without a GPU runs the whole suite on the CPU path. A GPU failure at runtime (driver error, OOM, unsupported op) falls back to CPU when `fallback="cpu"` (default for interactive use) and returns `ErrorInfo{code:"ACCEL_*"}` when `fallback="error"` (default in benchmarks, so a silent fallback can never pass a GPU runtime gate).

### 2. Backend protocol
```text
oa_accel.select(policy) -> Backend          # name: "cpu" | "cuda" | "rocm"; device id; xp; caps
Backend.dense_solve(A, B, assume="gen"|"sym"|"pos")  # LU / Cholesky
Backend.sparse_solve(A_csr, B, method="auto"|"direct"|"cg"|"gmres", M=None, tol)
Backend.raster_stencil(...)                  # 5-/7-point FVM operators, matrix-free
Backend.memory: MemoryBudget                 # see §3
Backend.report() -> AccelReport              # device, driver, library versions, peak bytes, fallbacks
```
`AccelReport` goes into every result's provenance and into the job cache key (device class and library major version only, so a cached CPU result is not reused as a GPU result when bitwise identity is claimed, and vice versa).

### 3. Device selection policy
Order of precedence: explicit argument → setup/config `accel.device` → env `OA_DEVICE` (`auto`, `cpu`, `cuda`, `cuda:N`, `rocm`, `rocm:N`) → `auto`.

`auto` picks a GPU only when **all** hold, otherwise CPU:
1. CuPy imports and reports ≥ 1 device; NVIDIA compute capability ≥ 7.0 (cuDSS and current CuPy wheels support older parts, but we do not test them), AMD gfx targets on AMD's ROCm support list.
2. The problem is above the measured crossover for that operation (WP-304 records it; initial defaults: dense N ≥ 1500, sparse nnz ≥ 2e6, FDTD ≥ 2e5 cells). Below it the CPU is faster once transfer and kernel-launch overhead is counted.
3. The memory estimate (§4) fits the GPU budget.
4. The job holds the machine's **GPU lease**: `Resources.gpu = True` already exists in `oa_framework.analysis.Resources`; `oa_jobs` runs at most `OA_GPU_SLOTS` (default 1) tasks with `gpu=True` at once. Multi-GPU: one task per device, never one task across devices in M1P.

Laptops with hybrid graphics: the discrete device with the most free memory wins; `OA_DEVICE=cuda:N` overrides.

### 4. GPU memory budgeting
- Budget `OA_GPU_MEM_GB`, default `min(0.75 × total, total − 2 GB)` (12 GB on a 16 GB card), applied with `cupy.get_default_memory_pool().set_limit(size=...)` before the first allocation; pinned host memory gets its own pool limit (`OA_GPU_PINNED_GB`, default 2 GB) and is counted in the task's host `memory_gb` budget.
- Every accelerated operation provides `estimate_bytes(problem)` and refuses up front (`ACCEL_OUT_OF_MEMORY`, with the estimate and the budget in `ErrorInfo.hint`) instead of discovering OOM mid-solve. Estimates are checked in tests: the measured peak (`pool.total_bytes()` high-water mark) must lie within 0.8-1.25× of the estimate on the benchmark cases.
- Host RAM caps (TASKS rule 10) are unchanged and still apply to GPU jobs: staging buffers, sparse factorization on the host side of cuDSS hybrid mode, and result arrays count against `max_memory_gb`.
- Long kernels are chunked so that no single launch exceeds ~1 s (Windows WDDM TDR resets the driver after 2 s by default on a display GPU [verify current default]). FDTD issues one launch per time step and is unaffected.

### 5. Determinism and GPU-vs-CPU tolerance rules
- **Same device, same library versions, same input → bitwise-identical output.** Kernels use no floating-point `atomicAdd`; reductions use fixed-order tree reductions (CuPy/CUB `reduce` with a fixed block size) or Kahan-compensated per-block sums combined on the host in index order. cuSPARSE/cuDSS algorithms with non-deterministic modes are configured for deterministic variants where the library offers them; where it does not, the operation is labelled `deterministic=False` in `AccelReport`.
- **GPU vs CPU are not bitwise equal.** Gates compare them by tolerance, per class:

| Class | Precision | Gate between GPU and CPU result |
|---|---|---|
| Dense LU/Cholesky (BEM) | float64 | relative difference of C, L ≤ 1e-10 (condition number ≤ 1e6 on the B-IMP cases) |
| Sparse direct (DC IR, thermal) | float64 | node voltage max abs difference ≤ 1e-9 × V_set; KCL residual ≤ 1e-9 on both |
| Sparse iterative (CG/GMRES) | float64 | both converge to ‖r‖/‖b‖ ≤ 1e-10; solutions differ ≤ 1e-7 relative |
| FDTD (ADR-0010) | float32 fields, float64 accumulators | \|ΔS\| ≤ 1e-4 linear (−80 dB) vs the float64 CPU engine on the verification set; derived Z0 ≤ 0.01 % |
| Raster/stencil | float64 | ≤ 1e-12 relative |

- The engineering accuracy targets of [BENCHMARKS.md](../BENCHMARKS.md) apply to every backend independently; the table above only ensures the backends agree with each other far below those targets.

### 6. Library coverage per solve

| Solve | CPU (always) | NVIDIA | AMD (ROCm) | Gaps and notes |
|---|---|---|---|---|
| Dense LU/Cholesky, BEM (N ≈ 1e3-2e4) | LAPACK via SciPy | cuSOLVER `getrf/getrs`, `potrf` via `cupy.linalg` / `cupyx.lapack` | rocSOLVER through CuPy's hipified path | Batched small systems (B-IMP-04): `cupyx` batched solve on NVIDIA; on ROCm use a loop of streams if batched entry points are missing [verify per CuPy release] |
| Sparse SPD direct (DC IR, thermal, 1e5-1e7 unknowns) | CHOLMOD (`scikit-sparse`) / PARDISO (`pypardiso`) / SuperLU | **cuDSS** via `nvmath.sparse.advanced.DirectSolver` (nvmath-python; the module is marked experimental; CSR with int32 indices) ([nvmath docs](https://docs.nvidia.com/cuda/nvmath-python/latest/host-apis/sparse/generated/nvmath.sparse.advanced.DirectSolver.html)). cuDSS supports Linux and Windows x86_64 ([cuDSS docs](https://docs.nvidia.com/cuda/cudss/)) | **No maintained sparse direct solver reachable from Python.** Use iterative (next row) | `cupyx.scipy.sparse.linalg.spsolve` is a single-RHS cuSOLVER QR path and is not used for production sizes |
| Sparse SPD iterative | `pyamg` AMG-PCG | `cupyx.scipy.sparse.linalg.cg` with a Jacobi or IC0 preconditioner; **AMGX** (BSD-3, via `pyamgx`) as an optional extra for AMG-PCG | `cupyx` CG over hipSPARSE with Jacobi; rocALUTION is C++ only (no Python binding we can rely on) | AMG on ROCm is a gap: Jacobi-PCG needs more iterations; the benchmark reports the ROCm column separately |
| Nonsymmetric sparse (AC PDN, M2+) | SuperLU / PARDISO | cuDSS (general LU) | GMRES (`cupyx`) + ILU0 if available | |
| Stencil / raster FVM | NumPy | CuPy `ElementwiseKernel` | same source | none |
| FDTD | NumPy reference engine (ADR-0010) | CuPy `RawKernel` | same source via hiprtc | ADR-0010 |
| FFT (TDR, NF2FF later) | `scipy.fft` | cuFFT via `cupy.fft` | rocFFT via `cupy.fft` | none |

### 7. Platforms

| Platform | NVIDIA | AMD |
|---|---|---|
| Windows native | Supported, tier 1: CuPy CUDA wheels exist for Windows; cuDSS supports Windows 10/11 x86-64 ([cuDSS release notes](https://docs.nvidia.com/cuda/cudss/release_notes.html)) | **Not supported in M1P.** AMD's HIP SDK for Windows (7.2.0, page dated 2026-08-20) ships the ROCm math and primitives libraries but not the communication or AI libraries ([HIP SDK component support](https://rocm.docs.amd.com/projects/install-on-windows/en/latest/conceptual/component-support.html)); CuPy documents no Windows ROCm build ([CuPy install guide](https://docs.cupy.dev/en/stable/install.html)). Windows AMD users get the CPU path |
| WSL2 (Windows host) | Supported, tier 1 for Palace (§8); CUDA is visible inside WSL2 with the Windows driver (verified on the reference machine) | Tier 2: AMD lists WSL support for specific Radeon cards (RX 7900 XTX/XT/GRE in the current matrix) with ROCm 7.2.1 and the ROCDXG path; `rocm-smi` is not supported under WSL ([AMD WSL compatibility](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/compatibility/compatibilityrad/wsl/wsl_compatibility.html)). Memory queries fall back to `hipMemGetInfo` |
| Linux | Tier 1 | Tier 2 (experimental CuPy ROCm; `amd-cupy` wheels) |
| macOS | CPU only | CPU only |

Tier 1 = in the nightly GPU lane and in the M1P gate. Tier 2 = documented, smoke-tested when hardware is available, benchmark column reported but not gating.

### 8. Palace on the GPU
- Palace builds GPU support itself: `-DPALACE_WITH_CUDA=ON` (NVIDIA) or `-DPALACE_WITH_HIP=ON` (AMD or NVIDIA), `CMAKE_CUDA_ARCHITECTURES` / `CMAKE_HIP_ARCHITECTURES`, optional `PALACE_WITH_MAGMA` (libCEED MAGMA backend) and `PALACE_WITH_CUDSS` (requires CUDA; `CUDSS_DIR` must point at a cuDSS archive install, not a pip/conda wheel); Spack example `spack install palace +mumps +slepc +cuda +cudss cuda_arch=80,90` ([Palace install](https://awslabs.github.io/palace/stable/install/)). At run time `config["Solver"]["Device"] = "GPU"` and, normally, the default `config["Solver"]["Backend"]` (the libCEED backend Palace picks for the device) ([Palace parallelism and GPU guide, v0.16](https://awslabs.github.io/palace/v0.16.0/guide/parallelism/)); cuDSS is selected with `Linear.Type = "cuDSS"`.
- Our build: WP-502 builds Palace 0.18.1 with `PALACE_WITH_CUDA=ON`, `CMAKE_CUDA_ARCHITECTURES=89`, MAGMA and cuDSS inside WSL2 on the reference machine, under the same `systemd-run` memory/CPU scope as the CPU build, and documents the HIP recipe (`PALACE_WITH_HIP=ON`, `CMAKE_HIP_ARCHITECTURES=gfx1100`) without gating on it (no AMD hardware here).
- Palace runs stay external processes (ADR-0004). The `gpu=True` hook of `PalaceAdapter` sets `Solver.Device` and requests the GPU lease.

### 9. What is not accelerated in M1P
gmsh meshing (CPU, worker processes, ADR-0005); KiCad parsing, ReferenceMap and ERC loops (CPU-bound, branchy: Rust per ADR-0008/WP-508/WP-509); openEMS (no GPU engine; research 05 §1.2).

## Consequences
- One code path for NVIDIA and AMD at the source level, at the price of AMD being second-tier wherever the Python ecosystem lacks bindings (sparse direct, AMG).
- Every accelerated operation has three obligations: a CPU oracle, a memory estimate, and a tolerance class in §5. Reviewers reject GPU code that lacks any of them.
- `oa_jobs` gains a GPU slot limit and `AccelReport` provenance; this is additive (CR to be filed by WP-503 against interfaces.md for `AccelReport` in provenance).
- CI: a self-hosted nightly GPU lane on the reference machine (`.github/workflows/wp-503-gpu.yml`), never on shared runners; GPU tests carry the `gpu` marker and are skipped unless `OA_RUN_GPU=1`, which only the holder of the heavy/GPU token sets (TASKS rule 11).
- Risk: CuPy ROCm stays experimental. If AMD's `amd-cupy` wheels lapse, the AMD tier degrades to CPU without code changes.
