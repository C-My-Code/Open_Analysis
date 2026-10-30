# oa-fdtd: theory, error budget and validation

GPU/CPU finite-difference time-domain solver for PCB regions (ADR-0010). Code: `packages/oa-fdtd/src/oa_fdtd/`. The NumPy engine (`reference.py`) is the test oracle and CPU fallback; the CUDA engine (`engine.py`, kernels under `kernels/`) implements the same equations. See the [theory index](../../docs/theory/README.md).

## 1. Problem statement

**Input.** A `Model`: a non-uniform rectilinear grid (mesh lines [m]); regions with $\varepsilon_r$, conductivity and Debye poles; copper boxes (PEC or surface-impedance); ports (lumped, or quasi-TEM mode ports); six outer walls (`pec | pmc | cpml`); an excitation waveform; the frequencies of interest [Hz]. The `EMProblem` front end (`subcell/pcb.py`, `build_model`) turns a board region into such a model.

**Output.** Running DFTs of probe voltages, from which `run.sparams` returns power-wave S-parameters $S_{ij}(f)$ (dimensionless) at the port planes, or `ports.wave_sparams` returns S from modal waves on three planes per port with the reference plane at the port.

It is a full-wave time-domain solver: it includes dispersion, radiation, higher-order modes and 3D discontinuities, within the limits of the grid.

## 2. Governing equations and formulation

### 2.1 Yee scheme on a graded grid

Maxwell's curl equations on a staggered Yee lattice (Yee 1966), second order in space and time:

$$\varepsilon\,\partial_t\mathbf E=\nabla\times\mathbf H-\sigma\mathbf E-\mathbf J_p,\qquad \mu_0\,\partial_t\mathbf H=-\nabla\times\mathbf E .$$

Cell sizes vary from cell to cell (`Grid(x, y, z)`; lines built by `oa_mesh.rect`). Inverse primal and dual lengths enter the differences. The E update uses a per-edge material table with coefficients `ca`, `cb` (semi-implicit conductivity, `compile_model`: with $\ell=\sigma\Delta t/2\varepsilon$, $c_a=(1-\ell)/(1+\ell)$ and $c_b=(\Delta t/\varepsilon)/(1+\ell)$). Edge permittivity is the volume average of the neighbouring cells, stored as a `uint16` index (0 = PEC).

### 2.2 Time step and stability (CFL)

`Grid.dt` (`grid.py`) uses the smallest cell on each axis:

$$\Delta t=\frac{S}{c_{max}\sqrt{\dfrac1{\Delta x_{min}^2}+\dfrac1{\Delta y_{min}^2}+\dfrac1{\Delta z_{min}^2}}},\qquad c_{max}=\frac{c_0}{\sqrt{\varepsilon_{r,min}}},\quad S=0.95\ (\text{Courant factor, }\texttt{Model.courant}).$$

$\varepsilon_{r,min}$ is the smallest permittivity among non-PEC regions and 1 (`compile_model`). The classical limit is $S\le1$ for the uniform Yee scheme; on a non-uniform grid using the smallest cell on each axis is conservative. Stability is therefore by construction, as long as the material table does not contain a speed above $c_{max}$. Other time steps in the code base: `oa_mesh.rect.estimate_timestep` uses 0.99 (a helper for estimates; the solver itself uses `Grid.dt`).

**Instability detection.** `run.run` raises `ModelError("FDTD.UNSTABLE")` if the field energy becomes non-finite, or if, after the excitation has decayed, it rises more than `growth_db` (default 20 dB) above its post-excitation minimum (the ADR-0010 late-time growth detector). Slower growth below that threshold is not caught.

### 2.3 Boundaries: CPML

Convolutional PML with complex-frequency-shifted stretching (Roden and Gedney 2000), applied as a correction after the bulk update (`pml.py`). Per slab and per curl term the derivative $d$ is replaced by $d/\kappa+\psi$, $\psi\leftarrow b\psi+c\,d$, with a polynomially graded profile of order $m=3$:

$$\sigma_{max}=-\frac{(m+1)\ln R_0}{2\eta_0 L},\quad \kappa=1+(\kappa_{max}-1)\rho^m,\quad \alpha=2\pi f_{low}\varepsilon_0(1-\rho),\quad b=e^{-(\sigma/\kappa+\alpha)\Delta t/\varepsilon_0},$$

with $\rho$ the normalised depth, $L$ the layer thickness, defaults 10 cells, $m=3$, $\kappa_{max}=8$, $R_0=10^{-8}$ (the design reflection at normal incidence), $f_{low}=10^8$ Hz. PEC and PMC walls are also available (PMC by a mirror ghost layer). The $\psi$ arrays exist only inside the slabs.

### 2.4 Dispersive dielectrics (ADE)

Multi-pole Debye, $\varepsilon(\omega)=\varepsilon_\infty+\sum_p\Delta_p/(1+j\omega\tau_p)$, as an auxiliary differential equation per edge (`kernels/physics.cu`, mirrored in `reference.py`):

$$E^{n+1}=c_aE^n+c_b\Big(\nabla\times H-\tfrac12\sum_p(1+k_p)J_p^n\Big),\qquad J_p^{n+1}=k_pJ_p^n+\beta_p\,\Delta E,\quad k_p=\frac{2\tau_p-\Delta t}{2\tau_p+\Delta t},$$

with the static part $\Delta t\beta_p/2$ added into $\varepsilon$ in the material table. `materials.fit_debye` fits the fewest passive poles (non-negative weights) to a target $\varepsilon(f)$ over the band and reports `err_eps` and `err_tand`; `fit_djordjevic_sarkar` and `fit_callable` are wrappers.

### 2.5 Copper: SIBC

Copper boxes have PEC interiors. Tangential E on a box face is set from the outside H samples through a surface impedance (`subcell/sibc.py`). The impedance of a finite slab with the far side shorted,

$$Z_s(\omega)=\frac{k}{\sigma}\coth(kt),\qquad k=\frac{(1+j)}{\delta},$$

times a roughness factor $K(f)$ (Hammerstad-Jensen), is fitted by a passive R-L ladder $R_0+\sum_pR_p\frac{j\omega\tau_p}{1+j\omega\tau_p}$ with non-negative $R_p$. Each section is discretised with a bilinear (trapezoidal) transform, which is unconditionally stable, and applied by recursive convolution. E is written at $t_{n+1}$ from $H^{n+1/2}$, a half-step phase of $\omega\Delta t/2$ (documented as negligible).

### 2.6 Ports

- **Lumped ports.** A resistor of value $R$ spread over the port edges, with semi-implicit loss and a soft source.
- **Mode ports** (`ports/modal.py`, `ports/build.py`). A static quasi-TEM template from a 2D electrostatic solve on the port plane, with bilinear (Q1) elements on the Yee lattice so that edge voltages are exact node differences. Capacitance matrices with and without dielectric give the modes: $C_0^{-1}Cu=\varepsilon_{eff}u$ and $Z=1/(v\,u^TCu)$. The port conductivity follows the dielectric (a local admittance $\kappa\varepsilon_e$).
- **Wave planes** (`ports/deembed.py`). Modal voltages on three planes per port give the propagation constant from $\cosh(\gamma d)=(V_1+V_3)/(2V_2)$, independent of the reflection, and then forward and backward waves referred to the port plane. This cancels port-region loss and reflection; the plain port S does not (0.2 dB offset at 10 GHz in the README).

### 2.7 S extraction and the $\cos(\omega\Delta t/2)$ port correction

For batch member $k$ driving port $j$ with source DFT $V_s$ and port resistance $R$:

$$a_j=\frac{V_s}{2\sqrt{R_j}},\qquad S_{ij}=\frac{V_i/\sqrt{R_i}-\delta_{ij}a_j}{a_j},\qquad V_i=\mathrm{DFT}(E^{n+1})\cdot\cos(\omega\Delta t/2),$$

`run.sparams`; all N excitations are run as a batch and $S=BA^{-1}$.

The factor $\cos(\omega\Delta t/2)$ (`port_voltage_factor` in `run.py`) exists because the lumped-port loss is semi-implicit: the resistor carries the time average $(E^n+E^{n+1})/2$, which in the frequency domain is $\cos(\omega\Delta t/2)$ times the recorded $E^{n+1}$ DFT (the DFT carries absolute time stamps, so there is no phase). Without it the port voltage, and so $|S|$, is wrong by $1/\cos(\omega\Delta t/2)$, an $O(f^2)$ drift: $3.8\times10^{-3}$ at 20 GHz for 0.5 mm cells and $S=0.95$. It was first mistaken for a port-discretisation error. The same factor applies to mode ports.

### 2.8 Run control and precision

The run ends when the field energy is `end_db` (default -50 dB) below its peak, checked every 64 steps, or at `max_steps`. Probes are summed on the device in float64 with a fixed reduction order, so same-device runs are bitwise reproducible; the DFT is float64 even in float32 mode. The default dtype is float32 on the GPU; the NumPy engine runs float64.

## 3. Assumptions and validity range

- **Grid resolution.** Numerical dispersion of the Yee scheme is $O(h^2)$; `oa_mesh.rect` caps cells at $\lambda_{min}/N$ in the densest dielectric with $N=15/20/30$ (coarse/medium/fine) and 1/3-2/3 placement at metal edges. Accuracy near the upper band edge falls as $(kh)^2$. The README shows the Yee dispersion within $10^{-3}$ of the exact relation (float64) for its test grid.
- **Staircasing.** Dey-Mittra conformal PEC is **not implemented**. Via barrels and diagonal polygons are staircased, so via inductance and capacitance carry a geometric error that shrinks only with cell size. No convergence study for this exists yet.
- **CPML.** The reflection figure ($-84$ dB) is measured at normal incidence only; oblique incidence and evanescent fields (a source or port close to the CPML) are not validated. Port faces next to CPML need padding.
- **Mode ports.** Static quasi-TEM: no full-wave 2D eigenmode solve; one band-centre $\varepsilon$ for the template and port impedance. Not valid where the port cross-section supports significant dispersion or higher modes at the sweep frequencies.
- **SIBC copper.** Valid where the skin depth is small compared with the conductor feature size and curvature. The ladder fits Re $Z_s$ of the roughness model to 2 to 3 % at 1 µm rms; it cannot follow the sharp Hammerstad step.
- **Low frequency.** A DC-free pulse has little energy below about 0.1 $f_{max}$, so S below that is noisy; the low-frequency S11 from the port S (not the planes) reaches $-26$ dB in the B-EM-01 case.
- **Not implemented**: anisotropy (glass weave), magnetic materials, lumped RLC elements (ignored with a diagnostic), Mur ABC, field dumps, subgridding, near-to-far-field transform, wire models, matrix-pencil tail extrapolation for high-Q structures.
- Material sets above 65535 averaged entries are refused.

## 4. Error sources and magnitudes

| Source | Magnitude | Evidence |
|---|---|---|
| Time/space discretisation, cavity | error < 0.05 %, observed order 2.0 (Richardson over 3 grids), TE101/TE011/TM110 | README verification case 1 |
| CPML reflection at normal incidence | worst $-84$ dB over 0.1 to 40 GHz; late-time energy < $-80$ dB | README case 2 |
| Parallel-plate TEM line, lumped ports | S11 < $-45$ dB; $\||S_{21}|-1\|<10^{-4}$ up to 20 GHz at 0.5 mm cells | README case 3 (`tests/golden/fdtd/tem_line`) |
| Port normalisation drift before the cos correction | $3.8\times10^{-3}$ at 20 GHz | `run.port_voltage_factor` docstring |
| Debye ADE (Djordjevic-Sarkar 4.3, 8 poles) | $S_{21}$ within 0.02 dB (0.05 dB mode ports); phase 1 degree | README case 4 |
| SIBC copper plates (2e5 S/m, 0.25 mm) | loss within 2 % of the analytic ABCD line; phase 0.1 degree | README case 5 |
| Closed-box energy, $10^5$ steps | float64 < $10^{-6}$; float32 CUDA $3.3\times10^{-7}$ | README case 7 |
| float32 against float64 | within $10^{-4}$ on probe DFTs; GPU float64 within $10^{-10}$ | README |
| Debye fit of Djordjevic-Sarkar | `err_eps`, `err_tand` reported per fit; value depends on band and pole count; not tabulated here [verify per model] | `materials/debye.py` |
| Staircased vias, no conformal PEC | **not quantified** | README known gaps |
| Input error (Dk, Df, thickness, roughness) | **not quantified in this package** | planned UQ, ADR-0012 |

## 5. Validation evidence

**Analytic and internal.** Cases 1 to 5 and 7 of ADR-0010 section 9 in the table above, run on both engines (CUDA needs `OA_RUN_GPU=1`). Golden suites: `tests/golden/fdtd/{cavity,cpml,tem_line}` and `tests/golden/fdtd_physics/{ds_line,sibc_line,microstrip_thru}`.

**Cross-solver / converged reference (B-EM-01).** A 25 mm microstrip thru (0.38 mm line on 0.2 mm FR-4 Djordjevic-Sarkar, 35 µm copper), wave ports, medium mesh (78 624 cells, 12 160 steps, -70 dB end criterion), float32 on an RTX 4080 SUPER, against the cascaded converged 2D FEM RLGC (`oa_impedance`, same material) at the modal port impedance 47.9 ohm:

- insertion-loss error 0.005 dB at 10 GHz, 0.038 dB at 20 GHz, worst 0.038 dB over 0.2 to 20 GHz (target 0.05 dB: met);
- max $|S_{11}|$ $-28.4$ dB (target below $-29$ dB where the reference is below $-30$ dB: **missed by 0.6 dB**);
- 1.6 s run time, 0.02 GiB GPU memory.

The reference is the project's own FEM, so this is a cross-method check inside the project (both use the same dielectric model), not an independent measurement.

**Performance (B-PERF-01).** 6.0 Gcells/s float32 and 3.2 float64 on 256^3 with 10-cell CPML (target >= 3.0); NumPy float64 0.014 Gcells/s.

**Known gaps and open issues.**

- **B-EM-03** (40 GHz differential via transition, the case that exercises staircased vias) has **not been run**. FOLLOWUPS (WP-506A) says this explicitly. B-EM-02 (stripline), B-EM-04, B-EM-05, B-EM-06 are not run for oa-fdtd either.
- The B-EM-01 S11 target is missed (-28.4 dB against -29 dB).
- The FDTD benchmark harness pages (`docs/validation/benchmarks/em.md`, `perf.md`) still read "oa-fdtd does not exist yet" and show B-PERF-01 as pending; they were generated before oa-fdtd merged and disagree with the README measurements above. The README and FOLLOWUPS numbers are the more recent.
- `tests/golden/fdtd/tem_line/case.toml` still carries the pre-fix comment that the S21 deviation "grows as f^2 above 3 GHz (3.8e-3 at 20 GHz)"; the cos correction (WP-506) removed this (FOLLOWUPS). The case only lists frequencies up to 3 GHz, so this golden file itself stops at 3 GHz; the 20 GHz claim (|S21|-1 below 4e-5, FOLLOWUPS) rests on a test I did not locate [verify].
- `FDTD.UNSTABLE` covers non-finite energy and a 20 dB late-time rise (configurable `growth_db`). Matrix-pencil tail extrapolation (`oa_fdtd.tail`) is applied when a run hits `max_steps`.
- Mode ports use a local 2D solver, not `oa_impedance` (change request WP-506-modal-solver); the two could disagree on the port impedance, which is not cross-checked.
- PERF-01 for a PCB-like case (ADE, CPML, special edges) is not measured; the quoted throughput is for the plain kernel only.
- ROCm is untested on hardware.
- CPML is validated at normal incidence only.

## 6. References

- K. S. Yee, "Numerical solution of initial boundary value problems involving Maxwell's equations in isotropic media", *IEEE Trans. Antennas Propag.* 14(3), 1966, pp. 302-307.
- A. Taflove, S. C. Hagness, *Computational Electrodynamics: The Finite-Difference Time-Domain Method*, 3rd ed., Artech House, 2005.
- J. A. Roden, S. D. Gedney, "Convolution PML (CPML): an efficient FDTD implementation of the CFS-PML for arbitrary media", *Microwave Opt. Technol. Lett.* 27(5), 2000, pp. 334-339.
- J. H. Beggs, R. J. Luebbers, K. S. Yee, K. S. Kunz, "Finite-difference time-domain implementation of surface impedance boundary conditions", *IEEE Trans. Antennas Propag.* 40(1), 1992, pp. 49-56 [verify pages; the code marks them [verify]].
- S. Dey, R. Mittra, "A conformal finite-difference time-domain technique for modeling cavities and waveguides" [verify]; the code names the "Dey-Mittra" scheme as not implemented.
- R. J. Luebbers, F. Hunsberger, "FDTD for Nth-order dispersive media", *IEEE Trans. Antennas Propag.* 40(11), 1992 [verify].
- A. R. Djordjevic et al., *IEEE Trans. EMC* 43(4), 2001 (dielectric model; see [oa-physics THEORY](../oa-physics/THEORY.md)).
- C. Warren et al. (2019), the gprMax GPU throughput reference quoted in BENCHMARKS.md B-PERF-01 and ADR-0010: full citation [verify], not reproduced here.
- Project: `docs/adr/0010-gpu-fdtd-engine.md` (design and verification cases), `docs/BENCHMARKS.md` section 3 (B-EM-01 to B-EM-06, B-PERF-01), `docs/research/05-fullwave-em.md`.
