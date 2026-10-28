# ADR-0004: Solvers as jobs, solver-neutral problem IR, subprocess adapters, mandatory quality gates

- Status: Accepted (2026-10-27)

## Context
- Full-wave solvers have very different shapes:
  - openEMS: FDTD, Windows-native, GPL-3, one run per excitation, no MPI since v0.37;
  - Palace: FEM, Apache-2.0, Linux-only, MPI/GPU, JSON + gmsh;
  - EMerge, later.
- Quasi-static extraction (FasterCap, FastHenry2, Palace ES/MS, Elmer) and 3D thermal (Elmer) have the same integration shape.
- Runs take minutes to hours. They must be cancellable, resumable, cacheable and runnable elsewhere (WSL, Docker, Slurm).
- Research 03 and 05 found that unchecked S-parameters (non-passive, non-causal, missing DC) are the most common source of wrong downstream results.

## Decision
- **One geometry IR for all 3D work.** `Structure3D` comes from `oa_geometry.cut_region`. On top of it sit the solver-neutral problems `EMProblem`, `QSProblem` and `ThermalProblem` (interfaces.md §5).
- **Adapters.** `SolverAdapter` and `ExtractionAdapter` expose `capabilities`, `check`, `prepare` (pure file generation into a job directory) and `collect` (parse outputs). Adapters never run solvers in-process.
- **Order:** openEMS first, then Palace in M1, then FasterCap/FastHenry2/Palace ES-MS in M2, then EMerge and Elmer in M3.
- **Jobs.** Every analysis is a DAG of content-addressed `Task`s run by `oa_jobs`. There are two pluggable executor layers:
  - worker executors (`inline`, `process`) for Python code;
  - external runners (`local`, `wsl`, `docker`, then `ssh-slurm`) for solver binaries.

  Tool locations come from `tools.toml`, never from setups.
- **Quality gates are structural.**
  - A `NetworkResult` cannot be constructed without a `QualityReport` (passivity, reciprocity, causality, DC point).
  - `oa_jobs` rejects bundles that skip the gate.
  - Failed networks mark the bundle `failed_quality`, and time-domain consumers refuse them by default.
  - `ExtractionResult` requires `validity_fmax` and a matrix passivity record.
- **Pinned versions.** The solver version is part of the cache key. Containers pin Palace 0.18.x, and the openEMS minimum is 0.37.

## Consequences
- Adapters are testable without the solver (snapshot `prepare`, committed raw outputs for `collect`). Real-solver tests run nightly under the `external` marker.
- openEMS N-port runs farm out naturally as N tasks. Palace on Windows is a runner choice (WSL or Docker), not a code path.
- Cross-solver validation (openEMS vs Palace) costs only a second adapter run against the same `EMProblem`.
