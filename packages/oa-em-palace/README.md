# oa-em-palace

Palace (AWS, Apache-2.0, FEM on MFEM) solver adapter, WP-301. Implements
`oa_framework.em.SolverAdapter` (interfaces.md section 5.3). Palace runs as a **separate process**
(WSL2, Docker or native Linux) and is never imported here. Registered as `palace` in the
`open_analysis.em_adapters` entry point group.

## Public API

```python
from oa_em_palace import PalaceAdapter, build_config, discover, host_to_wsl

adapter = PalaceAdapter(mpi_ranks=2, gpu=False, memory_gb=8)  # hooks: ranks, GPU flag, memory box
adapter.capabilities()  # modal wave ports, lumped, coax; absorbing/pec/pmc; all excitations in one run
adapter.check(problem)  # unsupported features as Diagnostics
run = adapter.prepare(problem, workdir, tool_info)  # mesh (WP-206) + config.json + 1 command
out = adapter.collect(problem, run, results)  # EMSolveOutput: network (+ .sNp), convergence log
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

- `systemd-run --user --scope -p MemoryMax=<OA_PALACE_MEM_GB>G -p MemorySwapMax=0 -p CPUQuota=400%`
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

## Validation (microstrip thru, 25 mm, 0.38 mm on 0.2 mm FR-4)

Palace 0.18.1, wave ports, coarse WP-206 mesh (228k tets, 293k DOFs), order 1, AMS, 2 MPI ranks,
3 sweep points; openEMS numbers are the WP-205 lumped-port results.

| Quantity | openEMS (WP-205) | Palace | Tolerance |
|---|---|---|---|
| IL at 10 GHz | 0.74 dB | 1.24 dB | 0.6 dB |
| IL at 1 GHz / 5.5 GHz | - | 0.15 / 0.72 dB | loss grows with f |
| Pozar closed form, 10 GHz | about 1.0 dB | (same) | |
| \|S11\| (modal reference) | below -20 dB | below -36 dB | -30 dB |
| delay | within 5 % of Hammerstad-Jensen | within 5 % | 5 % |
| Z0 | 50.7 ohm | not exported (see below) | Hammerstad-Jensen 50.7 ohm checked |

The 0.5 dB excess over openEMS is mostly dielectric loss: Palace holds tan d fixed at its 5 GHz
value (0.020) and the two-sided sheet conductor model adds conductor loss; Pozar's closed form
lies between the two solvers. Z0: Palace 0.18 exports no Z0(f) for wave ports unless GSLIB
(`VoltagePath`) is built in, so the check is S11 against the port's own modal reference.

Cost (one full run, 2 excitations, 3 frequencies, through the WSL runner and `oa-palace-run`):
runtime 169 s (pytest module total 166 s including meshing), peak memory 3.6 GB (cgroup peak of
the whole tree, limit 6 GB). An 11-point lumped-port run took 548 s and 4.0 GB. The sparse
direct solver (Palace's default) is not usable on a 16 GB-class machine at this size: it is why
the adapter defaults to `Solver.Linear.Type = "AMS"` (GMRES, 28 to 61 iterations per frequency).

## Resolved [verify] items (WP-206 notes, against Palace 0.18.1)

- **Mesh format**: MFEM's gmsh reader in Palace 0.18.1 reads **`.msh` 2.2 only**; a 4.1 file aborts
  with "vertices indices are not unique". The adapter writes `msh_version="2.2"`.
- **Length unit**: `Model.L0 = 1.0` for a mesh in metres works (Palace default is 1e-6); lengths
  in the file (`Offset`, `Thickness`) are then metres. The lumped-port surface resistance Palace
  derives (95 ohm/sq for R = 50 ohm, 0.38 x 0.2 mm) confirms it.
- **Attributes**: the physical-group tags of the 2.2 file are the attributes (volumes `mat:*`,
  surfaces everything else); no physical name is used.
- **Interior sheets**: `Conductivity` (with `Thickness`, `External`) on zero-thickness interior
  sheets and `PEC` on them are both accepted and solve; wave-port mode analysis takes the sheets as
  PEC through `WavePortPEC`. The loss difference between sheet and PEC conductor is below what the
  fixture resolves.
- **Wave-port faces**: a port face made of several coplanar triangle groups (one group, several
  materials, copper cross-section cut out) works as one `WavePort` attribute.
- **Absorbing faces next to lumped ports**: a lumped port on a face whose remainder is absorbing
  loses about 3 dB (|S21| = -4 dB at 1 GHz) because the first-order boundary shunts the line end.
  Use wave ports for FEM microstrip; the lumped fixture is not a valid Palace comparison.
- **Excitation**: `"Excitation": true` puts every port in excitation group 1; the adapter writes
  one integer group per excited port to get a full S matrix in one run.
- **Sweep**: `Samples.NSample` is the number of points (end points included); `NSample = 1` fails.
- **Output**: `postpro/port-S.csv` header is `f (GHz), |S[i][j]| (dB), arg(S[i][j]) (deg.)`,
  `S[i][j]` = port i response to excitation j; AMR writes `error-indicators.csv`.

## WP-501 findings (microstrip IL)

The 1.24 dB above is mostly the first-order absorbing boundary 1 mm / 2.5 mm from the line (about
+0.25 dB at 10 GHz); with 5 mm / 6 mm padding Palace p2 gives 0.949 dB (BEM 0.845). See
`docs/change-requests/WP-501-palace-il-root-cause.md` for the table, the new banded-dielectric
option (`palace.banded`, `eps_tol`, `tand_tol`), the `occ.*` mesh keys and the `PALACE.ABC_NEAR`
warning. Memory: p2 above about 1 M DOF with AMS exceeds a 12 GB box.

## Known gaps

- Differential/coupled wave ports, coax ports and rough-copper impedance boundaries are not
  validated (roughness is ignored with a warning); far field and field dumps are not implemented.
- The stripline and via-transition fixtures are covered by config snapshots only; their
  Palace-vs-openEMS acceptance (|dS21| <= 0.5 dB to 20 GHz, stripline Z0 within 2 % of BEM) and the
  AMR / adaptive-sweep options are not exercised. A real run of the via fixture needs a much larger
  mesh than the 6 GB box allows at order 2.
- Dielectrics are fixed at one frequency (no broadband models in Palace 0.18).
- Wave-port S-parameters are referenced to the modal impedance; `port_impedance` is not returned.
- The Docker image and GPU (`gpu=True`) paths are untested (no Docker or CUDA Palace here).
- The Windows `wsl` runner test needs a Palace in WSL; CI runners without it skip.
- `oa_testkit.plugin.tool_available("palace")` only searches the host PATH; the adapter's
  `tests/conftest.py` lifts that skip when `discover()` finds Palace (CR WP-301).
