# oa-em-openems: theory, error budget and validation

Adapter that turns an `EMProblem` into an openEMS (FDTD) run and the run's port data into S-parameters. The solver (openEMS 0.37.0) runs as a separate process and is never imported. This page covers what the **adapter** does and what is known about the accuracy of the result. The Yee/CPML numerics belong to openEMS; for the in-repo FDTD see [oa-fdtd](../oa-fdtd/THEORY.md). Code: `packages/oa-em-openems/src/oa_em_openems/` (`mesh.py`, `scene.py`, `script.py`, `dispersion.py`, `postprocess.py`, `touchstone.py`). See the [theory index](../../docs/theory/README.md) and [oa-em](../oa-em/THEORY.md).

## 1. Problem statement

**Input.** An `EMProblem`: 3D structure (copper bodies, dielectric slabs, vias), ports (lumped or wave), boundaries, frequency plan [Hz], mesh settings, convergence settings.

**Output.** `EMSolveOutput`: an N-port network (written to Touchstone, RI, 12 digits), a port impedance table $Z_0(f)$, a convergence log (field energy versus step), mesh statistics (cells, min/max cell, grading, estimated memory/time, Debye fit errors, mesh hash), and a quality report from `oa_network.quality.assess`.

## 2. Governing equations and formulation

### 2.1 Solver

openEMS integrates Maxwell's curl equations with the Yee FDTD scheme on a (possibly graded) rectilinear grid, with PML or Mur absorbing boundaries, in the time domain; port quantities are Fourier-transformed after the run (Liebig 2013). Time stepping and CFL are handled inside openEMS and are not controlled by the adapter [verify the openEMS internal Courant factor].

### 2.2 Mesh (`mesh.py`, `scene.py`)

- Maximum cell $d_{max}=\lambda_{min}/N$, $\lambda_{min}=c_0/(f_{max}\sqrt{\varepsilon_{r,max}})$, with $N=10/20/30$ for `coarse/medium/fine` (`_PRESET_CPW` in `scene.py`) or `cells_per_wavelength`.
- Third rule at axis-aligned metal edges: 1/3 cell inside, 2/3 outside.
- At least 4 cells across every dielectric layer and across via diameters.
- Growth limited to `max_grading = 1.3` away from fine regions; up to about 1.5 across fixed lines, with the achieved value in the stats.
- Graded lines are spaced evenly in $\int dx/\text{target}$, as in [oa-mesh](../oa-mesh/THEORY.md).

### 2.3 Materials

**Copper.** By default a `ConductingSheet` with the real conductivity and thickness, placed on the dielectric face or mid-layer; or thick PEC (`openems.conductor_model = "pec"`). The sheet is a zero-thickness surface in the grid carrying a finite conductivity-thickness product; its physical content is the DC sheet resistance $1/(\sigma t)$ with skin-effect treatment determined by openEMS [verify]. Antipad polygons become keyhole polygons. Vias are solid PEC cylinders (no plating thickness, no hollow barrel).

**Dielectrics.** Because openEMS time-steps Debye materials directly but a Djordjevic-Sarkar response has no finite pole set, the adapter fits (`dispersion.fit_debye`, non-negative least squares)

$$\varepsilon(\omega)=\varepsilon_\infty+\frac{\Delta\varepsilon}{1+j\omega\tau}+\frac{\kappa}{j\omega\varepsilon_0}$$

(one Debye pole plus a conductivity term) over $[f_{max}/20,\ f_{max}]$ and reports the fit error. The alternative `openems.dispersion = "constant"` uses band-centre constants.

### 2.4 Ports and S extraction (`postprocess.py`)

Ports record total voltage $V$ and current $I$ at the measurement plane. Power waves, with a real per-port reference $z$:

$$a=\frac{V+zI}{2\sqrt z},\qquad b=\frac{V-zI}{2\sqrt z},\qquad S=BA^{-1},$$

where column $k$ of $A$ and $B$ is run $k$ (excitation of port $k$). This renormalises exactly to any real reference and does not assume the other ports were matched during run $k$. Wave ports are openEMS transmission-line probes (`MSLPort`, `StripLinePort`, "tl_probe") with the feed line added outside the box (length $\max(4W,6h,8\text{ cells})$); the reference plane is on the box face, moved by `deembed` with the line's own impedance and phase constant. If `z0=None`, the reference is the median measured line impedance; $Z_0(f)$ is returned separately.

### 2.5 Excitation and termination

Gaussian pulse without DC content (`openems.pulse = "bandpass"`, default). The baseband pulse of the openEMS tutorials leaves static charge on strips that no port drains. The run stops at the energy criterion (`convergence.energy_decay_db`) or at `max_timesteps`. Absorbing faces are `PML_8` (8-cell PML) or MUR (`openems.absorbing = "MUR"`).

## 3. Assumptions and validity range

- **Sheet copper.** No finite thickness: edge currents, trace thickness effect on capacitance and the field inside copper are absent. Z0 differs from a 35 µm trapezoid (below).
- **Single-pole Debye.** A single pole cannot follow Djordjevic-Sarkar's flat loss tangent. The adapter reports the fit error and warns above 10 %. The measured error is about 11 % in tan-delta across 0.5 to 10 GHz (FOLLOWUPS); about 10 % across a 20:1 band (README).
- **Pulse and low frequency.** The DC-free pulse gives noisy S below about $0.1f_{max}$; the flow raises the minimum frequency ([oa-em](../oa-em/THEORY.md)).
- **Ports.** Microstrip and symmetric stripline wave ports and lumped ports only. CPW, coax and coupled/differential wave ports are errors; multi-conductor and multi-mode ports unsupported; every port must be excited (S assembled from all N runs).
- **Stripline.** Closed-box runs stall near -33 dB energy (slow decay of low-frequency energy in the PML) and give unreliable, non-passive S-parameters at the iteration cap. Validated only for microstrip with lumped ports.
- **Far field** (NF2FF) is behind a flag and not validated against a reference antenna.
- **Roughness** is not modelled (copper sheet is smooth).
- **est_time_s** is pessimistic (316 s estimated against 21 s actual) before the flow's 0.1 scale.

## 4. Error sources and magnitudes

| Source | Magnitude | Evidence |
|---|---|---|
| Sheet copper versus 35 µm trapezoid, Z0 | 50.73 ohm (openEMS) against 48.72 ohm (BEM, 35 µm trapezoid): +4.1 % (FOLLOWUPS says 3.8 %); against thin-strip BEM 50.95 ohm: -0.43 % | `em.openems.z0`, `em.openems.z0_35um` |
| IL at 10 GHz against BEM RLGC cascade (23 mm) | 0.689 dB against 0.778 dB | `em.openems.il_10ghz`, gate 0.2 dB, pass |
| IL at 10 GHz, 25 mm | 0.74 dB (openEMS) against 0.845 dB (BEM, WP-501) | `oa-em-palace/README.md`, FOLLOWUPS |
| Matched-line S11 | -33.8 dB | `em.openems.s11_max` |
| Debye fit | ~11 % tan-delta over 0.5 to 10 GHz | FOLLOWUPS |
| Numerical grid error, cell count, CFL | not separately quantified; refinement check gives max $|\Delta S|$ if requested | `EM.MESH_NOT_CONVERGED` |
| Run time | 31 s to 10 GHz (sheet copper) in BENCHMARKS B-EM-01; 34 s, 4 threads in the validation run | BENCHMARKS, `results.json` |

## 5. Validation evidence

- `collect()` on committed raw data reproduces `expected.s2p` to $10^{-9}$ (`tests/golden/em_openems/raw/microstrip_thru`). This checks post-processing, not the physics.
- Snapshots of `prepare()` for three EMProblem fixtures pin the generated scripts (regression, not accuracy).
- `external("openems")` (nightly, needs openEMS): microstrip thru end to end; cross-solver numbers above.
- No tutorial reproduction (MSL notch filter, patch antenna) has been run; those slots are `pending` in the validation page. B-EM-01/02/05/06 have no gated openEMS result.

**Known gaps and open issues.**

- Stripline wave-port runs stall near -33 dB and give non-passive S (unresolved; B-EM-02 lossy case is the test that would catch a fix).
- Z0 differs from 35 µm copper because the default is sheet copper; thick-copper modelling is not implemented.
- A multi-pole Debye fit is needed to improve the 11 % tan-delta error or the band limit must be stated.
- The MSL-notch tutorial reproduction and the wave-port microstrip Z0-versus-Hammerstad-Jensen acceptance check are not done.
- Far field untested. CPW, coax, coupled ports unsupported.
- The README's statement of 10/20/30 cells per wavelength for coarse/medium/fine differs from the 15/20/30 of `oa_mesh.rect` used by oa-fdtd.
- CR: `EMSolveOutput` versus `NetworkResult` quality requirement; the adapter forces a `fail` verdict if no assessor is available (`WP-108-solver-output-quality-gate.md`).

## 6. References

- T. Liebig, A. Rennings, S. Held, D. Erni, "openEMS - a free and open source equivalent-circuit (EC) FDTD simulation platform supporting cylindrical coordinates suitable for the analysis of traveling wave MRI applications", *Int. J. Numer. Model.* 26(6), 2013, pp. 680-696 [verify].
- openEMS documentation: https://docs.openems.de ; MSL notch and patch antenna tutorials (planned reproductions).
- K. Kurokawa, "Power waves and the scattering matrix", *IEEE Trans. MTT* 13(2), 1965.
- Project: `docs/adr/0004-solver-integration.md`, `docs/research/05-fullwave-em.md`, `docs/change-requests/WP-108-*.md`.
