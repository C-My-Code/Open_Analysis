# oa-em-palace: theory, error budget and validation

Adapter for Palace (AWS, Apache-2.0), a finite-element frequency-domain Maxwell solver built on MFEM. Palace runs as a separate process (WSL2, Docker or native Linux) and is never imported here. This page states what the adapter hands to Palace, what is known about the accuracy of the result, and where the evidence is thin. Code: `packages/oa-em-palace/src/oa_em_palace/` (`config.py`, `adapter.py`, `parse.py`, `discovery.py`, `paths.py`) and the 3D mesher `oa_mesh.occ3d` ([oa-mesh](../oa-mesh/THEORY.md)). See the [theory index](../../docs/theory/README.md) and [oa-em](../oa-em/THEORY.md).

**Short version of the evidence:** the only completed accuracy study is one 25 mm microstrip thru. After fixing a boundary-placement error Palace's insertion loss at 10 GHz is 0.949 dB against 0.845 dB from the 2D reference (0.10 dB apart); the benchmark gate is 0.05 dB. Nothing above 10 GHz, no stripline, via, antenna or filter case has been run in Palace.

## 1. Problem statement

**Input.** An `EMProblem` (structure, ports, boundaries, frequency plan [Hz], mesh and convergence settings).

**Output.** S-parameters $S_{ij}(f)$ parsed from `postpro/port-S.csv` (dB and degrees converted to complex; never-excited columns filled by reciprocity), an AMR error-indicator history as the convergence log, mesh statistics, wall time, peak memory, and the quality report.

## 2. Governing equations and formulation

### 2.1 Palace's model (from Palace documentation [verify against v0.18.1])

Driven (frequency-domain) problem: for each frequency, find the electric field $\mathbf E$ with

$$\nabla\times\big(\mu_r^{-1}\nabla\times\mathbf E\big)+j\omega\mu_0\sigma\,\mathbf E-\frac{\omega^2}{c_0^2}\,\varepsilon_r\,\mathbf E=0\quad(\text{plus port and boundary terms}),$$

discretised with $H(\mathrm{curl})$-conforming (Nedelec) edge elements of order $p$ on tetrahedra (`Solver.Order`), with ports and absorbing boundaries as boundary integral terms. The resulting complex sparse system is solved iteratively (GMRES) with an auxiliary-space Maxwell solver (AMS) preconditioner (`Solver.Linear.Type = "AMS"`) because the default sparse direct solver does not fit in memory at this size. Adaptive mesh refinement (AMR) uses an error indicator; "fast sweep" (`AdaptiveTol`) builds a reduced-order model over the frequency band.

### 2.2 What the adapter writes (`config.py`)

| Item | Mapping |
|---|---|
| Mesh | `oa_mesh.occ3d.mesh_problem`, metres, gmsh 2.2 format (MFEM's reader in Palace 0.18.1 reads only version 2.2), `Model.L0 = 1`. |
| Dielectrics | One `Materials` entry per `mat:*` group, each evaluated at $f_{max}/2$ (`palace.eps_freq`) because Palace 0.18 has no frequency-dependent materials. A warning `PALACE.DIELECTRIC_FIT` states the variation over the band (Djordjevic-Sarkar: $\varepsilon$ about 1 %, tan-delta about 0 to 3 %). A banded-dielectric option (`palace.banded`, `eps_tol`, `tand_tol`) exists after WP-501. |
| Copper | Default: sheet. Each `cond:*` and `via:*` group gets `Boundaries.Conductivity` with real $\sigma$ and the real thickness for zero-thickness sheets (a finite-thickness surface impedance); `palace.conductor_model = "pec"` makes them perfect. Roughness is not modelled (`PALACE.ROUGHNESS_IGNORED`). |
| Boundaries | `pec`/`pmc` direct; `absorbing` is a first-order absorbing boundary (second order with `palace.abc_order = 2`); `pml` becomes an absorbing boundary with a warning (no PML in 0.18). |
| Ports | Lumped: `LumpedPort` with $R=z_0$. Wave: numeric `WavePort` (mode 1, `Offset = deembed`) with `WavePortPEC` on the conductors; the 2D modal eigen-solve on the port face gives the mode and its modal impedance. Coax: cylindrical lumped port (untested). |
| Sweep | `Driven.Samples` linear or log; `f_min = 0` starts at $0.1f_{max}$ (`PALACE.NO_DC`); AMR with `mesh.amr_max_passes` and `convergence.delta_s`; `mesh.fem_order` sets `Solver.Order`. |

### 2.3 S-parameters

Palace reports $S$ referenced to the **modal** impedance of each wave port. The wave-port $Z_0(f)$ is not exported (needs a Palace build with the voltage-path option, per the README), so the adapter cannot return `port_impedance` and checks S11 against the port's own modal reference instead.

### 2.4 Safe execution

All runs go through `scripts/oa-palace-run`: `systemd-run --user --scope -p MemoryMax=<OA_PALACE_MEM_GB>G -p MemorySwapMax=0 -p CPUQuota=400%`, `nice -n 19`, MPI ranks clamped (default 2). This is a process-control measure, not a numerical one; it exists because an uncapped sparse-direct run of about 300k DOF froze the machine (FOLLOWUPS).

## 3. Assumptions and validity range

- **Dielectrics fixed at one frequency.** The material tan-delta and Dk are evaluated at $f_{max}/2$; across a wide band the loss at the band edges is wrong by the DS variation (up to a few percent in tan-delta) [measured per the README note]. For a sweep out to 20 GHz the loss error grows with the distance from the centre.
- **Absorbing boundary.** The first-order ABC is accurate only for waves hitting it near normal incidence and when it is far from the radiating structure; a distance of 1 mm to 2.5 mm from the line cost about 0.25 dB at 10 GHz (WP-501). The adapter now warns `PALACE.ABC_NEAR`.
- **Wave ports only for microstrip.** A lumped port on a face whose remainder is absorbing loses about 3 dB (|S21| = -4 dB at 1 GHz) because the first-order boundary shunts the line end; the lumped microstrip fixture is not a valid Palace comparison.
- **Copper loss.** The finite-thickness conductivity boundary is one-sided (about 0.07 dB at 10 GHz of the remaining 0.10 dB discrepancy, FOLLOWUPS); the interior-sheet boundary condition is untested in Palace beyond the accepted run.
- **Differential/coupled wave ports, coax ports, rough copper**: not validated.
- **Far field and field dumps**: not implemented.
- **Solver cost.** Order 2 above about 1 M DOF with AMS exceeds a 12 GB box. The converged microstrip run took 597 s and 7.8 GB on CPU; a p2 run on the full 228k-tet mesh exceeded 12 GB.
- **No GPU path**: Palace stays CPU-only (no CUDA toolkit in WSL, M1P decisions).

## 4. Error sources and magnitudes

All values are for the 25 mm microstrip thru (0.38 mm on 0.2 mm FR-4, Djordjevic-Sarkar, 35 µm copper) at 10 GHz.

| Source | Effect on IL | Evidence |
|---|---|---|
| First-order ABC too close (1 mm / 2.5 mm), p1 coarse mesh | about +0.25 dB, largest term; total IL was 1.24 dB | `docs/change-requests/WP-501-palace-il-root-cause.md`, FOLLOWUPS |
| After padding to 5 mm / 6 mm, p2 | 0.949 dB against 0.845 dB BEM RLGC, 0.10 dB apart | FOLLOWUPS WP-501 |
| One-sided finite-thickness copper impedance | about 0.07 dB | FOLLOWUPS WP-501 |
| Single-frequency dielectric (DS fixed at 5 GHz against the reference) | about 0.03 dB | FOLLOWUPS WP-501 |
| Mesh order and AMR | no p3 or AMR convergence run; not quantified | FOLLOWUPS |
| $|S_{11}|$ (modal reference) | below -36 dB | `oa-em-palace/README.md` |
| Group delay | within 5 % of Hammerstad-Jensen | README |
| Lossless/linear solver tolerance (`linear_tol`) | not quantified | none |

The reference is the project's own 2D BEM/FEM RLGC cascade, not a measurement.

## 5. Validation evidence

- Config snapshots for three EMProblem fixtures (`tests/golden/em_palace/config_*.json`) pin the generated `config.json` (regression only).
- `collect` on committed raw output reproduces the S matrix (parser test).
- `external("palace")` (slow, `OA_RUN_SLOW=1`): the 25 mm microstrip thru through the real WSL runner, compared with the openEMS result. Numbers: IL 1.24 dB (coarse, p1, near ABC) against openEMS 0.74 dB; later 0.949 dB (p2, padded ABC) against 0.845 dB BEM.
- Benchmark B-EM-01 gate (0.05 dB, converged R1/R2 reference): **not met, not gated** (`ref-pending` in `docs/validation/benchmarks/em.md`). B-EM-02 to B-EM-06 for Palace: not run.

**Known gaps and open issues.**

- Accuracy: remaining 0.10 dB IL gap to the reference is not apples-to-apples (WP-510 owns the fix); there is no p3/AMR convergence study and nothing above 10 GHz; the B-EM references under `tests/benchmarks/references/em/` do not exist; roughness not implemented.
- `LossTan = 0` hangs the wave-port mode solve.
- The wave-port `z0` is not honoured and not exported.
- The stripline and via Palace acceptance cases are not run (config snapshots only); an AMR or adaptive-sweep run has not been exercised. The via fixture needs a mesh larger than the 6 GB box allows at order 2.
- Windows WSL runner test requires Palace in WSL; CI skips it. The Docker image and GPU path are untested.
- **Documentation inconsistency inside the package README:** the validation table attributes the 0.5 dB Palace-versus-openEMS excess "mostly" to dielectric loss (tan-delta fixed at 5 GHz plus the two-sided conductor model); the section "WP-501 findings" later shows the dominant cause is the nearby first-order ABC (about 0.25 dB). The latter supersedes the former.
- **Length-basis mismatch between sources:** FOLLOWUPS WP-301 quotes the BEM RLGC IL as 0.78 dB, WP-501 as 0.845 dB. The ratio of 0.845 to 0.78 is 1.083 and equals 25/23 (the line lengths of the two cases), so the two are probably the same line loss for different lengths [inference, not confirmed].

## 6. References

- Palace: https://awslabs.github.io/palace/stable/ (documentation; version used 0.18.1). Formal citation and the exact equations used by 0.18.1 [verify].
- MFEM: R. Anderson et al., "MFEM: a modular finite element methods library", *Comput. Math. Appl.* 81, 2021, pp. 42-74 [verify].
- J.-C. Nedelec, "Mixed finite elements in R^3", *Numer. Math.* 35, 1980, pp. 315-341 [verify].
- P. Monk, *Finite Element Methods for Maxwell's Equations*, Oxford University Press, 2003.
- J. Jin, *The Finite Element Method in Electromagnetics*, 3rd ed., Wiley-IEEE Press, 2014.
- Project: `docs/change-requests/WP-501-palace-il-root-cause.md`, `docs/adr/0009-gpu-acceleration.md` section 8, `docs/BENCHMARKS.md` B-EM-01 to B-EM-06.
