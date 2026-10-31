<!-- SPDX-FileCopyrightText: 2026 Open_Analysis contributors -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# WP-519 profile and results: B-IMP-04 BEM throughput (CPU and GPU)

Machine: Ryzen 9 9950X3D, RTX 4080 SUPER 16 GB, Windows 11. Proxy: the 383 distinct sections that
IM-002 meets on `tools/bench/gen_synth_board.py --tracks 1500 --mix` (mean 1423 panels, up to 3471),
times 50 log-spaced frequencies 1 MHz to 10 GHz, `ImpedanceServiceImpl.solve_many(method="bem")`,
cold process. Baseline: `main` at c658c8a. Timing runs used 8 solve threads (`OA_NUM_THREADS=8`).

## Profile of the baseline (one thread, sum of the stages = 82.8 s)

| Stage | Time | What it is |
|---|---|---|
| `build_problem` (mesh) | 10.2 s | 10 to 30 ms of Python and shapely per section, holds the GIL |
| influence P, D (native kernel, 8 threads) | 3.8 s | about 0.18 core-seconds per section: 2 logs + 3 atan2 per entry |
| vacuum solve (C0, sigma0) | 11.4 s | LU of the conductor block (bordered) |
| dielectric solve (assembly + `scipy.linalg.solve`) | 54.3 s, of which LU 48 s | LU of the whole (n+1)-system |
| R_ac(f) loss, per-frequency Python loop | 1.2 s | 50 frequencies x small matrices |
| modal decomposition, per-frequency `eig` | 1.2 s | 19 000 `numpy.linalg.eig` calls |

Findings:

1. **The two LUs are the same work twice.** The conductor rows of the system carry no permittivity,
   so the vacuum matrix is its leading block. 740 of 1900 GFLOP are the vacuum LU.
2. **`scipy.linalg.solve` holds the GIL in SciPy 1.18** (`_batched_linalg._solve`), so the four
   threads of `solve_many` ran one LU at a time: 52 s with 4 threads against 83 s with one.
   `lu_factor` / `lu_solve` (and `numpy.linalg.solve`) release it: 24 LUs of order 1500 take 0.64 s
   on one thread and 0.10 s on eight.
3. After the LU is parallel, **meshing is the longest stage and cannot be threaded** (GIL).
4. After that, **the native influence kernel is the CPU cost**: 70 core-seconds for 840 M matrix
   entries. It is in `oa-native` (not owned by this WP).
5. On the GPU the fp64 LU is the cost, not the launch: `getrf` of order 1634 takes 12 ms (136 to
   558 GFLOPS for n = 1000 to 3500), `getrs` with 160 columns 7 ms; 70 % device utilisation during
   the batch, i.e. device bound at about 6.5 s of kernels.

## Changes

- `bem/solver.py`: block elimination on one LU of `P_cc` shared by C0 and every C(f) anchor
  (THEORY.md 2.2); `getrf`/`getrs` through LAPACK wrappers that release the GIL and raise
  `LinAlgError` instead of a warning; loss factors and modal decomposition batched over
  frequencies (`modal.py`, `_loss`, `bem_to_result`); a fused CUDA kernel for P and D; the device
  path runs the same elimination under `oa_accel.solvers.guarded.run_guarded`; a parallel batch
  assembles small sections on the device and factors them on the host threads.
- `bem/meshpool.py`, `bem/meshworker.py`: worker processes mesh a large batch while the threads
  solve (subprocess + pickled frames; no `__main__` re-import; falls back to in-process meshing).
- `oa-accel`: `solvers/lu.py` (`lu_functions`), `solvers/guarded.py` (`run_guarded`).
- `geometry.py`: the proximity (gap) distance only evaluates the segments that are used, the
  conductor boundaries are built once (same meshes; checked panel by panel in `test_meshpool.py`).
- `service.py`: threads default to `OA_NUM_THREADS` (8 at most) instead of 4.

## Results (383 sections x 50 f, cold process, 8 solve threads, 4 mesh workers)

| Path | Before | After | Target |
|---|---|---|---|
| CPU (`OA_DEVICE=cpu`) | 52 s (4 threads, 4 BLAS threads each; 70 s under cProfile) | **10.5 s** (warm repeat 7.4 to 8.2 s) | 20 s |
| GPU (`OA_DEVICE=cuda`, device assembles P/D, host threads factor) | not measured; WP-504 README: 50 s for 1000 sections | **6.6 s** cold (CUDA start 0.8 s), **5.0 to 5.6 s** warm | 5 s |
| GPU, every section fully on the device (`OA_IMPEDANCE_BATCH_GPU_N=0`) | - | 9.4 s | - |

The GPU target is met only in the warm case and only within noise; the cold figure is 1.3x over.
Why not lower: in float64 this GPU (1/64 double rate) factors a 1500-panel system about as fast as
eight host cores, so it cannot carry the batch alone; the rest is main-process Python (about 12 ms
per section, GIL) and the host factorisations of the sections it does not take.

Rejected, with numbers:

- float32 `getrf` with float64 refinement on the device (`cupyx` LU, residual in float64): 17 s for
  the batch against 9.4 s. A refinement step costs about what the float64 `getrs` costs at order
  1500 to 3000 (latency bound), and 4 to 6 steps are needed for 1e-14.
- CUDA streams, one per section in flight: 9.4 s against 9.6 s, and the per-stream CuPy pool
  grew to the 12 GB budget (host `MemoryError` through the Windows commit cap).
- Splitting the batch between device and host sections by a hash of the section: no gain over the
  CPU path (6.1 s CPU-only vs 6.8 to 7.3 s), because the main process, not the factorisations, was
  the limit once the influence kernel moved to the GPU.

## Accuracy

Per-section maximum of `max|d| / max|ref|` over the 383 sections (reference: code at c658c8a):

| Array | CPU new | GPU new |
|---|---|---|
| R | 3.7e-14 | 4.0e-12 |
| L | 1.1e-14 | 1.1e-12 |
| G | 4.9e-10 | 4.9e-10 |
| C | 2.1e-10 | 2.1e-10 |
| Zc matrix | 5.8e-11 | 5.8e-11 |

C, G and Zc differ at the conditioning noise of the systems (the whole-system and the block
solution on the same host differ by as much: 1.4e-10 in C); R and L are stable to 1e-12. GPU against
CPU (new vs new) on the mix sections: at most 5e-10, the bound tested is 1e-9
(`test_gpu_matches_cpu_on_mix_sections`). No tolerance was loosened: the existing BEM golden suites
(`tests/golden/impedance_bem`, FEM cross-check, closed-form comparisons) pass unchanged. No float32
anywhere in the shipped path.

## Memory and kernels

- Device: CuPy pool peak 1.15 to 1.8 GB for the batch (B-IMP-04 limit 4 GB); the largest section
  (3297 panels) needs about 450 MB.
- Longest single device call: `getrf` of the 3297-panel section, 89 ms (a cuSOLVER call made of
  shorter kernels); the longest launch of our own is the influence kernel, 2.7 ms per 1272 rows
  (`_KERNEL_ENTRIES` = 4 M entries per launch). Far below the 1 s watchdog margin.
- Host: the section gate (`OA_IMPEDANCE_TOTAL_MB`, 1536) is unchanged.

## Determinism

The mesh is a pure function of the section (checked equal panel by panel), a section takes the same
path whatever the thread count, and `solve_many` returns results in input order. Two runs of the same
solve differ in the last digits (about 1e-13 of the largest entry) because BLAS kernels sum in an
order that depends on buffer alignment; the new tests compare at 1e-11 of the largest entry, not bitwise.

## Open items

- The native influence kernel (`oa-native`, not owned here) is the dominant CPU cost. P only needs
  the conductor rows and D the interface/ribbon rows; a kernel with row-wise flags (or three
  instead of five transcendental functions per entry through the shared subtended angle) would save
  about 20 to 40 % of it.
- Moving the whole section into the worker processes (mesh, influence, solve, loss) would remove
  the main-process GIL limit of the GPU path.
- `oa-accel/README.md` (not owned here) should list `solvers.lu` and `solvers.guarded`.
