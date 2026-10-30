# ADR-0010: Our own GPU FDTD engine, `oa-fdtd`, behind the solver-neutral adapter interface

- Status: Accepted (2026-10-29)
- Deciders: project owner (decision: "build our own GPU FDTD engine"), architecture owner
- Related: ADR-0004 (solvers as jobs, adapters), ADR-0005 (openEMS-style rectilinear grids stay outside gmsh), ADR-0009 (`oa-accel`), [BENCHMARKS.md](../BENCHMARKS.md), research 05
- Work packages: WP-505 (core engine), WP-506 (physics and ports), WP-507 (adapter and verification)

## Context
- openEMS is our broadband 3D workhorse. It is CPU-only (multithreaded SSE engine) and dropped MPI in v0.37 (research 05 §1.2). Under our thread caps the 25 mm microstrip thru takes 31 s to 10 GHz; a 4-port differential via to 40 GHz is estimated at 0.5-2 h (research 05 §5, unmeasured).
- Its adapter has known accuracy gaps (FOLLOWUPS: stripline runs stall near −33 dB with PML, single-pole Debye gives about 11 % tan δ error, sheet copper gives the thin-strip Z0, 4.1 % off the 35 µm BEM value).
- A GPU FDTD is a proven design: gprMax's CUDA engine reached 1194 Mcells/s on Kepler and 3405 Mcells/s on Pascal, up to 30× its OpenMP CPU solver (Warren et al., "A CUDA-based GPU engine for gprMax: open source FDTD electromagnetic simulation software", *Computer Physics Communications* 237 (2019) 208-218, [preprint](https://repository.uwl.ac.uk/id/eprint/5755/)). gprMax itself is a uniform-grid ground-penetrating-radar code and does not fit PCB multiscale geometry.
- The reference GPU (RTX 4080 SUPER, 16 GB) has about 736 GB/s of memory bandwidth [verify: NVIDIA product page]. FDTD is bandwidth-bound, so throughput is set by bytes moved per cell per step.

## Decision

### 1. Scope of `oa-fdtd`
A Yee-scheme FDTD solver for PCB regions, written for the GPU first, with a NumPy reference engine that is the test oracle and the CPU fallback (small problems only; openEMS stays the production CPU FDTD). M1P covers driven S-parameters to 40 GHz. NF2FF, field dumps for viewers and subgridding come after M1P.

### 2. Grid
- **Non-uniform rectilinear (graded) Yee grid.** Mesh lines come from a new shared module `oa_mesh.rect` (owned by WP-505), a port of the rules already proven in `oa_em_openems.mesh`: maximum cell λ_min/N in the densest dielectric (N = 15/20/30 for coarse/medium/fine), the thirds rule at metal edges (1/3 inside, 2/3 outside), at least 4 cells across every dielectric layer and via diameter, a cell at each copper face (§3), grading ≤ 1.3, snapping to layout features, and smoothing. The openEMS adapter may switch to `oa_mesh.rect` later; the rules are then one implementation, so cross-solver runs can share an identical grid.
- Coefficients are not stored per cell. Each edge stores a `uint16` index into a table of **averaged materials** (the unique combinations of the up-to-four cells around an edge, ε and σ averaged as openEMS does); per-axis 1D arrays hold the primary and dual inverse cell lengths. Kernels rebuild `ca, cb` on the fly.
- Time step: Courant limit for the non-uniform grid, `dt = S / (c_max · sqrt(1/Δx_min² + 1/Δy_min² + 1/Δz_min²))`, S = 0.95. Conformal cells (§3) clip their area fraction at 0.1 so that they do not shrink `dt`.

### 3. Conductors: thin copper, sub-cell and conformal treatment
- **Default `copper="thick_sibc"`:** mesh lines on both copper faces (35 µm on 0.2 mm FR-4 costs about 1.4× in `dt` against the ≥ 4 cells already required across the dielectric), tangential E zero inside the copper volume, and a **surface-impedance boundary condition** on every copper face. The sheet impedance of a finite slab, `Zs(ω) = k/σ · coth(k t)` with `k = (1+j)/δ` (so DC gives `1/(σt)`), multiplied by the roughness factor K(f) from `oa_physics` (Hammerstad or Huray), is fitted with a few real poles (vector fitting, error ≤ 0.5 % over the band) and applied by recursive convolution (Beggs, Luebbers, Yee and Kunz, "Finite-difference time-domain implementation of surface impedance boundary conditions", *IEEE Trans. Antennas Propag.* 40(1), 1992 [verify pages]). This addresses the 4 % thick-copper Z0 gap and gives frequency-dependent conductor loss including roughness, which openEMS's adapter does not model.
- `copper="sheet"`: one zero-thickness conducting sheet with the same fitted `Zs`; faster, and its Z0 equals the thin-strip value. It is allowed only when the B-EM accuracy check shows the thickness effect is inside the tolerance (t/h ≤ 0.02).
- `copper="pec"`: for verification and lossless cases.
- **Conformal PEC** for via barrels, arcs and 45° traces: Dey-Mittra locally conformal H update with partial cell areas and edge lengths (Dey and Mittra, "A locally conformal finite-difference time-domain (FDTD) algorithm for modeling three-dimensional perfectly conducting objects", *IEEE Microwave Guided Wave Lett.* 7(9), 1997 [verify pages]). Staircasing stays available as `conformal=False` for verification against openEMS, which staircases.
- Special edges (copper faces, conformal cells, ports) are held as compact index lists and updated by small correction kernels after the bulk update.

### 4. Boundaries
- **CPML** (convolutional PML with CFS stretching: Roden and Gedney, "Convolution PML (CPML): an efficient FDTD implementation of the CFS-PML for arbitrary media", *Microwave Opt. Technol. Lett.* 27(5), 2000, 334-339). Default 10 cells, polynomial grading m = 3, κ_max = 8 [tune], α_max set for the lowest band frequency. The CFS α is what removes the low-frequency late-time plateau that stalls openEMS stripline runs. ψ arrays exist only inside the PML slabs.
- PEC and PMC walls; first-order Mur only for verification against openEMS.

### 5. Materials: dispersive dielectrics through ADE
- Djordjevic-Sarkar (Djordjevic, Biljic, Likar-Smiljanic and Sarkar, "Wideband frequency-domain characterization of FR-4 and time-domain causality", *IEEE Trans. Electromagn. Compat.* 43(4), 2001, 662-667) and table dielectrics are fitted to an N-pole Debye sum with positive weights by vector fitting (Gustavsen and Semlyen, *IEEE Trans. Power Delivery* 14(3), 1999), N chosen automatically (typically 4-8 over 0.1-40 GHz) so that |Δε'|/ε' ≤ 0.2 % and |Δtan δ|/tan δ ≤ 2 % over the band. The fit is causal and passive by construction.
- Updated with the auxiliary differential equation (ADE) per pole (Taflove and Hagness, *Computational Electrodynamics: The Finite-Difference Time-Domain Method*, 3rd ed., Artech House 2005, ch. 9). The polarization-current arrays exist only on dielectric edges.
- Anisotropic εz/εxy (glass weave) is accepted by the material table but untested in M1P.

### 6. Ports, excitation and de-embedding
- **Lumped ports:** a resistive voltage source over one edge or a sheet of parallel edges (Piket-May, Taflove and Baron, "FD-TD modeling of digital signal propagation in 3-D circuits with passive and active loads", *IEEE Trans. Microw. Theory Tech.* 42(8), 1994).
- **Mode (wave) ports:** the port plane carries a modal template; total-field/scattered-field injection on the plane, CPML behind it. M1P templates are quasi-TEM modes computed from our own 2D solver (`oa_impedance` FEM/BEM on the port cross-section, E_t = −∇φ per mode, N modes for N signal conductors, so differential and coupled ports work). The template is valid while the port cross-section is ≪ λ; the adapter warns when it is not. A full-wave 2D eigenmode solve on the port slice (FDFD) is after M1P.
- V/I and modal amplitudes are accumulated by an on-device running DFT at the requested frequencies, in float64; S is computed with power waves, `S = B A⁻¹`, from all N excitations, exactly as `oa_em_openems.postprocess` does (so the result does not assume the other ports were matched).
- **Excitation:** modulated Gaussian without DC by default; a Gaussian with DC content for runs that need the DC point. **End criterion:** field energy decay of −50 dB (energy reduced every 64 steps), with a matrix-pencil extrapolation of the port signals as an option for high-Q structures (B-EM-07) [method to be validated in WP-506].
- **De-embedding:** reference-plane shift with γ(f) measured from two in-port probe planes, plus IEEE 370 2x-thru through `oa_network` when the setup provides a 2x-thru.
- **Multi-port batching:** the N excitations of an N-port run are a leading batch dimension of the field arrays (`(B, z, y, x)`), updated by the same kernel launch. Material indices and coefficients are read once per cell for all B members, and the B runs share one grid build and one kernel compile. The batch size is chosen from the memory estimate.

### 7. GPU kernel design
- **Precision.** Fields and ADE/CPML auxiliaries are float32; time, DFT accumulators and energy are float64. Every verification case (§9) also runs in float64 (NumPy engine, and a `dtype=float64` GPU build of the same kernels). On consumer Ada GPUs float64 arithmetic is a small fraction of float32 [verify ratio in the Ada whitepaper], but FDTD is bandwidth-bound, so float64 costs about 2-3×, not the arithmetic ratio. `precision="float64"` is a user option; `auto` switches to float64 when the requested end criterion is below −90 dB or the run exceeds 2e6 steps (accumulated round-off). The engine reports the float32 noise floor it reaches.
- **Layout.** Structure of arrays: one contiguous array per component, C order `[z][y][x]` with x fastest, rows padded to 32 elements (128 B) for coalescing.
- **Fused kernels.** One kernel updates all three H components and one updates all three E components (including ADE and the CPML ψ terms for cells in PML slabs). Each thread marches along z and keeps the previous plane in registers (2.5D blocking; Micikevicius, "3D finite difference computation on GPUs using CUDA", GPGPU-2, 2009). PCB regions are z-layered, so the z range is split into slab groups (air, plain dielectric, dispersive dielectric, copper layer). Each group launches a kernel template specialized for it (no ADE code in air), which avoids divergent branches.
- **Launch overhead.** Small grids are dominated by launches (about 5-10 µs each). Up to 64 steps are captured in a CUDA graph (CuPy stream capture) and replayed [verify graph capture under HIP].
- **Traffic model and targets.** The bulk update moves about 72 B per cell per step (read and write 6 field components, 6 B of material index, neighbour reuse from registers or L2), so 736 GB/s bounds throughput at about 10 Gcells/s. Targets are ≥ 3 Gcells/s for the plain kernel (30 % of that roofline) and ≥ 1.5 Gcells/s with ADE in all dielectric cells and CPML (benchmark B-PERF-01).
- **Memory model** (feeds the `oa-accel` estimator, ADR-0009 §4): 24 B fields + 6 B index per cell; + 12 B per Debye pole per dielectric cell; + 48 B per CPML cell; port/DFT storage is negligible. A typical PCB region (60 % dielectric, 6 poles) needs about 75 B per cell, so a 12 GB budget holds about 160 M cells for a single excitation, or about 60 M cells for a batch of 4.

### 8. Python and compiled parts
- **Everything ships as Python.** The orchestration, grid build, material fitting, port templates, DFT and post-processing are Python/NumPy. The kernels are CUDA C++ source files (`oa_fdtd/kernels/*.cu`) loaded as CuPy `RawKernel`s (with `ElementwiseKernel` for the simple per-cell ops), JIT-compiled by NVRTC on NVIDIA and hiprtc on ROCm from the same source, and cached by CuPy. Users need no compiler and there is no binary to build per platform.
- The NumPy reference engine implements the same equations in float64 (vectorized slices). It is about 100× slower than the GPU [estimate] and is used as the oracle and for grids ≤ 2e5 cells.
- No C++/Rust engine in M1P. A compiled CPU engine (Rust + rayon in `oa-native`) is considered only if B-EM CPU runtimes with openEMS miss their targets.
- License: our own implementation from the literature above, GPL-3.0-or-later. We read openEMS and gprMax (both GPL-3) for reference and attribute any adapted algorithm in the source.

### 9. Verification (gating WP-507, before any B-EM claim)
Analytic (float64 NumPy and float32 GPU both):
1. PEC rectangular cavity, uniform and graded grid: TE101/TE011/TM110 frequencies within 0.05 % after Richardson extrapolation over 3 grids; observed order 2.
2. CPML: plane-wave reflection ≤ −60 dB over 0.1-40 GHz at 10 cells; late-time energy decays below −80 dB, which tests the absence of a stripline-style stall.
3. Parallel-plate TEM line with PMC side walls and lumped or mode ports: Z0 = η·h/w exact to 0.1 %; |S21| = 1 to 1e-4 lossless.
4. Debye half-space: reflection vs the analytic Fresnel coefficient with ε(ω): ≤ 0.01 dB / 0.1°.
5. Conducting slab and sheet: transmission vs the analytic slab formula from DC to 40 GHz: ≤ 0.02 dB.
6. Coax (conformal) Z0 = 60/√εr · ln(b/a): ≤ 0.5 % at 10 cells across the gap; staircased for contrast.
7. Lossless closed box energy drift ≤ 1e-6 relative over 1e5 steps (float64), with the float32 drift reported.

Cross-solver: openEMS 0.37 on an identical grid (mesh lines from `oa_mesh.rect`, staircased PEC, constant materials): |ΔS21| ≤ 0.01 dB and |ΔS11| ≤ 1 dB where S11 > −40 dB, to 20 GHz. Then the B-EM accuracy targets of BENCHMARKS.md against the converged references.

### 10. Integration (solver-neutral)
- `oa_fdtd.adapter.FdtdAdapter` implements `oa_framework.em.SolverAdapter` and registers as `fdtd` in `open_analysis.em_adapters`. `oa em --solver fdtd` uses the same `EMProblem`, quality gate, Touchstone and sidecar path as openEMS and Palace.
- Per ADR-0004 the solver never runs in the analysis process. `prepare` writes `problem.json` + `grid.npz` into the job directory and returns one `ExternalCommand` `oa-fdtd run job.json` (a console script of the package; one command per batch). `collect` reads `ports.npz`. The task sets `Resources(gpu=True, memory_gb=<host estimate>)` and takes the GPU lease (ADR-0009 §3).
- Layering: `oa_fdtd` sits in the adapter layer with `oa_em_openems | oa_em_palace`. An import-linter contract keeps `oa_fdtd.engine` free of `oa_*` imports other than `oa_accel` and `oa_mesh.rect`, so the engine can later be split into its own distribution.

## Consequences
- We own a solver. That means numerical maintenance, a verification suite that must stay green, and documentation of every model choice. In exchange: GPU speed, thick-copper SIBC with roughness, a multi-pole causal dielectric, CPML, and the ability to fix what is wrong.
- openEMS stays as the independent CPU cross-check and the fallback for users without a GPU. Palace stays as the FEM reference and for high-Q work.
- Risks:
  - SIBC and conformal stability corner cases. Mitigation: verification cases 5 and 6, and a late-time energy-growth detector that aborts a run with `FDTD.UNSTABLE`.
  - The quasi-TEM port template above a few tens of GHz on thick dielectrics. Mitigation: the port-length variation check; full-wave port modes after M1P.
  - HIP compile differences. Mitigation: kernels restricted to the common subset, and a ROCm smoke lane when hardware exists.

## Amendments

### 2026-10-30 (WP-513): coarse mesh preset
The coarse mesh preset is now 10 cells per wavelength, matching `oa_fdtd`, openEMS and `oa_em`, so all three solvers see the same coarse grid. The occ3d FEM path (Palace) keeps its own 6/10/15 cells per wavelength for coarse/medium/fine.
