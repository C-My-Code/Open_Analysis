# oa-em-palace

Palace (AWS, Apache-2.0, FEM on MFEM) solver adapter, WP-301. Implements
`oa_framework.em.SolverAdapter` (interfaces.md section 5.3). Palace runs as a **separate process**
(WSL2, Docker or native Linux) and is never imported here. Registered as `palace` in the
`open_analysis.em_adapters` entry point group.

## Public API

```python
from oa_em_palace import PalaceAdapter, build_config, discover, host_to_wsl

adapter = PalaceAdapter(mpi_ranks=2, gpu=False, memory_gb=8)   # hooks: ranks, GPU flag, memory box
adapter.capabilities()   # modal wave ports, lumped, coax; absorbing/pec/pmc; all excitations in one run
adapter.check(problem)   # unsupported features as Diagnostics
run = adapter.prepare(problem, workdir, tool_info)   # mesh (WP-206) + config.json + 1 command
out = adapter.collect(problem, run, results)         # EMSolveOutput: network (+ .sNp), convergence log
```

`prepare` writes `workdir/{model.msh, model.groups.json, config.json}` and one `ExternalCommand`
(`palace config.json`, progress parser `palace`, output `postpro/port-S.csv`). `collect` parses
`postpro/port-S.csv` (dB / degrees to complex), fills never-excited columns by reciprocity, runs the
quality gate (`oa_network.quality.assess`; forced `fail` when unavailable, as in the openEMS adapter),
writes `out/<problem>.sNp` and `<problem>.json` (mesh stats, wall time, peak memory, quality) and
returns the AMR history (`error-indicators.csv`) as the convergence log.

## Translation (EMProblem to config.json)

- **Mesh**: `oa_mesh.occ3d.mesh_problem`, metres, so `Model.L0 = 1` (all lengths in the file are
  metres, frequencies are GHz). Attributes are the physical-group tags of `model.groups.json`.
- **Domains**: one `Materials` entry per `mat:*` group. Palace 0.18 has no frequency-dependent
  materials, so each dielectric is evaluated at `f_max/2` (`palace.eps_freq`) and a warning
  `PALACE.DIELECTRIC_FIT` states the variation over the band (Djordjevic-Sarkar: eps about 1 %,
  tan d about 0 to 3 %).
- **Copper (default: sheet)**: every `cond:*` / `via:*` group gets
  `Boundaries.Conductivity` with the real sigma and the real thickness for zero-thickness sheets
  (`Thickness`), `External: true` for full-size planes on the z faces. `palace.conductor_model =
  "pec"` makes them perfect. Roughness is not modelled (`PALACE.ROUGHNESS_IGNORED`).
- **Boundaries**: `pec`/`pmc` map directly, `absorbing` is first-order `Absorbing`
  (`palace.abc_order = 2`), `pml` becomes an absorbing boundary with a warning (no PML in 0.18).
- **Ports**: lumped -> `LumpedPort` (R = z0, direction from the positive to the negative
  terminal); wave -> numeric `WavePort` (mode 1, `Offset = deembed`) with `WavePortPEC` on the
  conductors; coax -> cylindrical lumped port (untested).
- **Sweep**: `Driven.Samples` linear/log, `f_min = 0` starts at `0.1 f_max` (`PALACE.NO_DC`);
  `frequency.adaptive` sets `AdaptiveTol` (fast sweep); `mesh.amr_max_passes` / `convergence.delta_s`
  set `Model.Refinement`. `mesh.fem_order` is `Solver.Order`.

Expert keys (`MeshSettings.expert`, prefix `palace.`): `conductor_model`, `abc_order`, `eps_freq`,
`linear_tol`, `solver`, `adaptive_tol`, `device`, `f_min_fraction`, `amr_update_fraction`.

## Running Palace safely (machine limits)

The WSL VM has no memory cap and the host job object cannot see inside it. The logical tool
`palace` is therefore the wrapper `scripts/oa-palace-run`, which every runner starts instead of the
Palace launcher:

- `systemd-run --user --scope -p MemoryMax=<OA_PALACE_MEM_GB>G -p MemorySwapMax=0 -p CPUQuota=200%`
  (fallback `prlimit --as`), `nice -n 19`, MPI ranks clamped to `OA_PALACE_MAX_NP` (2);
- stages a job on `/mnt/<drive>` to the Linux file system (9p is slow) and copies results back;
- prints `OA_PALACE_VERSION`, `OA_PALACE_WALL_S`, `OA_PALACE_PEAK_MEM_KB` (cgroup peak) for the
  sidecar.

`tools.toml`:

```toml
[tools.palace]
runner = "wsl"
distro = "Ubuntu"
path = "/home/<user>/opt/palace/bin/oa-palace-run"
```

`oa_em_palace.host_to_wsl` / `wsl_to_host` translate `D:\x` <-> `/mnt/d/x` and `\\wsl$\...`
paths. `config.json` holds only relative paths; the runner starts Palace in the job directory.

## Installing Palace (no sudo)

Palace ships no binaries and conda-forge has no package, so it is built from source. In WSL:

```
wsl -d Ubuntu -- bash /mnt/d/<repo>/docker/palace/install-wsl.sh
```

`docker/palace/install-wsl.sh` creates a micromamba toolchain (gcc, gfortran, Open MPI, OpenBLAS,
cmake) in `~/opt/mm`, builds Palace v0.18.1 into `~/opt/palace` under a memory/CPU scope with
`nice -n 19 make -j2`, and installs the wrapper. Only outbound HTTPS is needed. A Spack-based
`docker/palace/Dockerfile` (Palace 0.18.1) is provided for the `docker` runner; it was not built
here (no Docker on the development machine).

## Tests and golden data

- Unit (no Palace): config snapshots for the 3 EMProblem fixtures
  (`tests/golden/em_palace/config_*.json`, built on the committed WP-206 group sidecars
  `groups_*.json`), attribute coverage, adapter contract with meshing stubbed, WSL path mapping,
  WSL runner command line, wrapper limits, `port-S.csv` parsing, `collect` on committed raw output
  (`tests/golden/em_palace/raw/microstrip_thru`).
- `external("palace")` (slow, `OA_RUN_SLOW=1`): the microstrip thru fixture through the real WSL
  runner, compared with the openEMS result; see "Validation".
- Regenerate snapshots with `pytest --update-golden`.

RESULTS_PLACEHOLDER
