# oa-impedance: theory, error budget and validation

This page lets an engineer check the math behind `oa-impedance` and judge how far to trust its numbers. Code facts come from `packages/oa-impedance/src/oa_impedance/`. Test numbers come from `docs/validation/results.json` (run of 2026-10-29, commit `0cc834dc47ea`), `docs/BENCHMARKS.md` and `docs/FOLLOWUPS.md`. Anything not verified is marked [verify]. See the [theory index](../../docs/theory/README.md) for how this fits the wider chain of trust.

## 1. Problem statement

**Input.** A `CrossSection` (the canonical 2D cut of a board, from `oa_geometry` or built by hand): conductors (trapezoids, round wires, zero-thickness planes) with conductivity and roughness model, dielectric regions with a Dk/Df model (constant, Djordjevic-Sarkar, multi-pole Debye, table), a background Dk, and an optional frequency list. All lengths [m], frequency [Hz], conductivity [S/m].

**Output.** An `ImpedanceResult`:

- per-unit-length matrices R, L, G, C (`R` [ohm/m], `L` [H/m], `G` [S/m], `C` [F/m]) at each frequency (static = `[0.0]`, lossless);
- modal characteristic impedance, effective permittivity, delay [s/m], loss [dB/m];
- for two-conductor groups, Z0, Z_odd, Z_even, Z_diff, Z_comm [ohm].

The model is a 2D, quasi-static (quasi-TEM) transmission line. It does not compute 3D discontinuities, radiation or coupling between sections.

## 2. Governing equations and formulation

The service `ImpedanceServiceImpl._compute` (`service.py`) has three paths. `auto` tries the closed form first and falls back to the BEM when the closed-form validity is `invalid`.

### 2.1 Closed forms (`closed_form/`)

Published empirical or conformal-map formulas. Each returns a `CFResult` with `RangeCheck`s against the published validity range of the formula (`in_range`, `extrapolated` within 3x of the range, `invalid` beyond).

| Family | Formula (source named in code docstrings) |
|---|---|
| Microstrip | Hammerstad-Jensen 1980 (Z0, eps_eff, thickness correction); Wheeler 1977; eps_eff(f) Kirschning-Jansen 1982; Z0(f) Jansen-Kirschning 1983 |
| Embedded microstrip | Hammerstad-Jensen plus a cover correction with an exponent of 1.55; always flagged approximate |
| Stripline | Cohn 1954 exact for t = 0, Wheeler 1978 thickness correction; offset stripline as a parallel combination |
| CPW / CPWG | conformal mapping (Ghione-Naldi, Simons) with Gupta thickness correction |
| Edge-coupled | Kirschning-Jansen 1984 static even/odd set (with the 1985 errata); Cohn 1955 for stripline |
| IPC-2141 | legacy only (`legacy=True`), never chosen by `auto` |
| Loss | Wheeler incremental inductance for conductor loss; filling factor for dielectric loss |

For example, the Hammerstad-Jensen air-filled zero-thickness impedance (`hj_z01`) is

$$Z_{01}(u)=\frac{\eta_0}{2\pi}\ln\!\left(\frac{f(u)}{u}+\sqrt{1+\left(\frac{2}{u}\right)^2}\right),\quad f(u)=6+(2\pi-6)\,e^{-(30.666/u)^{0.7528}},\quad u=W/h .$$

Broadside-coupled stripline has no closed form here; it goes to the BEM.

### 2.2 2D BEM (`bem/`)

**Unknowns.** One constant charge density per flat boundary panel on the noded union of conductor outlines, dielectric interfaces and zero-thickness ribbons. Formulation: boundary-element method with piecewise-constant panels and collocation at panel centres.

**Kernels** (`bem/kernels.py`). For a panel with uniform line charge, the potential and the normal field have closed forms (no quadrature, including the self term):

$$\phi(\mathbf r)=-\frac{1}{2\pi}\int \ln r\,dl,\qquad E_n=\frac{1}{2\pi}\,\Delta\theta ,$$

with $\Delta\theta$ the angle the panel subtends at the field point. The principal value of the normal field on the panel itself is zero.

**System** (`_solve_eps` in `bem/solver.py`). Conductor panels carry the potential condition $\sum_j P_{ij}\sigma_j + \phi_0 = V_i$ (a free common potential $\phi_0$ is an extra unknown, closed by the condition that the total free charge is zero, so the ground conductor carries the return charge). Dielectric interface panels carry the jump condition

$$\bar\varepsilon_i\,\sigma_i+(\varepsilon_{1,i}-\varepsilon_{2,i})\sum_j D_{ij}\sigma_j=0,\qquad \bar\varepsilon=\tfrac12(\varepsilon_1+\varepsilon_2).$$

Floating conductors are extra unknowns with a zero-charge constraint. The Maxwell capacitance matrix $C$ (in units of $\varepsilon_0$) is the free-charge functional of the solution. Complex permittivity $\varepsilon=\varepsilon'-j\varepsilon''$ is used as is, giving $C(f)$ and $G(f)=-\omega\,\mathrm{Im}\,C$.

**Inductance.** $L_\text{ext}=\mu_0\varepsilon_0\,C_0^{-1}$, with $C_0$ from the vacuum (all dielectrics replaced by air) solve. This is exact for a TEM line in a homogeneous medium and the quasi-TEM definition for an inhomogeneous one.

**Conductor loss** (`_loss`). Surface-impedance perturbation using the vacuum charge distribution $\sigma^0$ (the generalised Wheeler incremental-inductance rule):

$$R_{ac}(f)=C_0^{-1}\Big[\sum_p R_s(f)\,K_p(f)\,\ell_p\,\sigma^0_p\sigma^{0\,T}_p\Big]C_0^{-1},\qquad R_s=\sqrt{\pi f\mu_0/\sigma} ,$$

with $\ell_p$ the panel length and $K_p$ the roughness multiplier of the face (top, bottom or side) the panel belongs to. DC resistance is $1/(\sigma A)$ per signal conductor (zero for zero-thickness conductors).

In `bem_to_result` (`service.py`) the loop quantities are assembled as

$$L(f)=L_\text{ext}+R_{ac}/\omega,\qquad R_{ii}=\sqrt{R_{dc,i}^2+R_{ac,ii}^2}.$$

So the internal inductance is taken equal to $R_{ac}/\omega$ (surface impedance with equal real and imaginary parts), and the DC and AC resistances are combined in quadrature on the diagonal. Both are modelling choices, not derivations; see section 4. Because $K$ multiplies $R_{ac}$ and $L=R_{ac}/\omega$, a rough conductor also gets a larger internal inductance, a non-causal real-$K$ model (see [oa-physics THEORY](../oa-physics/THEORY.md) section 2.3 for the causal alternative).

**Meshing** (`bem/geometry.py`, `MeshParams`). Panel size $s=\mathrm{clip}(\alpha d,\ s_{min},\ s_{max})$, with $d$ the distance to the nearest singular point (corner, ribbon end, junction) and $\alpha=0.4$ by default, limited near other conductors by `gap_ratio * alpha * d_gap`. Strips thinner than $2\times10^{-3}$ of their width, and `ribbon` planes, collapse to zero-thickness ribbons whose free charge is recovered from the interface operator; this is what keeps true thin strips ($t=10^{-6}$ of the reference) accurate. Open boundaries are truncated at `truncation = 20` characteristic lengths.

**Frequency dependence.** Dispersive dielectrics are solved at up to six log-spaced anchor frequencies and interpolated with a cubic spline in $\ln f$ (real and imaginary parts separately). Conductor loss is evaluated at every requested frequency. BLAS is limited to one thread (`threadpoolctl`).

### 2.3 Modal decomposition (`modal.py`)

At each frequency with $Z=R+j\omega L$, $Y=G+j\omega C$ the code takes the eigen-decomposition of $ZY$, with $\gamma=\sqrt{\lambda}$ on the branch with positive real part, and forms

$$Z_c=\Gamma\,Y^{-1},\qquad \Gamma=T\,\mathrm{diag}(\gamma)\,T^{-1}.$$

Delay is $\mathrm{Im}\,\gamma/\omega$, loss is $\mathrm{Re}\,\gamma$ converted to dB/m, and $\varepsilon_\text{eff}=(\text{delay}\cdot c_0)^2$. For a pair: $Z_\text{diff}=Z_{c,11}+Z_{c,22}-Z_{c,12}-Z_{c,21}$ and $Z_\text{comm}=(Z_{c,11}+Z_{c,22}+Z_{c,12}+Z_{c,21})/4$. A section without a ground conductor uses its last signal conductor as the reference (N-1 mutual modes; noted in the result).

### 2.4 2D FEM reference (`fem/`)

Registered as `method="fem"` only after `import oa_impedance.fem`. It is the independent second method used to check the BEM.

- **Electrostatics** (`fem/electrostatic.py`): P2 triangular elements on the `oa_mesh.mesh_cross_section` mesh; conductors are holes; one Schur-complement solve per conductor; complex permittivity per region. Open boundary: Robin condition $\partial\phi/\partial n=-\phi/r$ on a box 20 reference lengths wide for grounded sections; a floating Dirichlet box with zero net charge when there is no ground.
- **Refinement**: the mesh is rebuilt with $h_{max}/\sqrt2$ and a tighter corner size until the energy changes by less than `rtol = 1e-3` or `max_levels = 3`. This is global re-meshing, not element-wise error estimation. `accuracy_class` is `reference` only if it converged.
- **Eddy currents** (`fem/eddy.py`): longitudinal vector potential $A$ with a per-group voltage drop as an extra unknown. A conductor is meshed as a volume where $\delta\ge t/\mathrm{ibc\_ratio}$ (default 4); otherwise it is a surface with a Leontovich boundary condition $E=Z_sK$ with the second-order curvature correction $Z_s\,[1+(1-j)\delta\kappa/4]$ and the roughness factor per face.

### 2.5 Profile along a net (`profile/`)

`impedance.profile` samples a net with `oa_geometry.sample_net`, locates transitions by bisection (25 µm default position accuracy), merges samples into runs, de-duplicates by `CrossSection.key()` and calls `solve_many` once on the unique sections. It adds no new physics; its accuracy is that of the per-section solver.

## 3. Assumptions and validity range

| Assumption | When it fails | What the code does |
|---|---|---|
| Quasi-TEM, no dispersion in the BEM/FEM | When the wavelength in the dielectric is comparable to the section size. Limit reported: $f_{qs}=0.1\,c_0/(\sqrt{\varepsilon_{r,max}}\,h_\text{char})$ (`_quasi_static_fmax`) | Above $f_{qs}$: `validity = "extrapolated"` plus a note. The BEM does not switch to a dispersive model. |
| No dispersion for coupled lines or CPW (closed forms and BEM) | Higher frequency | Static values, stated in the result |
| Closed-form formulas hold only inside their published parameter ranges | W/h, eps_r, t/h outside the fit range | `RangeCheck` flags; `invalid` makes `auto` fall back to the BEM |
| Embedded-microstrip exponent | Always approximate (no primary source reached) | Flagged approximate |
| Open-domain truncation at 20 characteristic lengths | CPW in free space (no bottom ground) converges slowly: Z0 error grows about 0.1 % per 3 mm of substrate depth at fixed S+2G = 2 mm | Documented; CPWG and anything over a reference plane is unaffected |
| Anisotropic `dk_z_over_dk_xy` | Glass-weave anisotropy | Ignored by BEM and FEM (noted) |
| Thin copper | Zero-thickness ribbons carry the total sheet current of both faces together in the loss integral; zero-thickness conductors have no DC resistance in the FEM | Documented |
| Surface-impedance loss model (BEM) | Conductor thinner than a few skin depths, or strongly non-uniform current | The BEM uses $R_s$ of a semi-infinite conductor. For thin copper at low frequency use the FEM eddy solve (`with_fem_loss`), which meshes the interior. |
| Real-valued roughness multiplier, $L_\text{int}=R_{ac}/\omega$ | Causality of rough-copper inductance; quantitative size of the effect not measured in this repo [verify] | See oa-physics for the causal surface impedance. The BEM does not use it. |
| Single mode per eigenvalue ordering | Near mode crossings the ordering of modes can swap | For N = 2 the order is `[even-like, odd-like]` |

## 4. Error sources and magnitudes

**Model error (formula or physics).**

- Closed forms are fits. Hammerstad-Jensen quotes about 1 % over its range [verify: the 1 % figure is the published claim, not re-measured here]. Measured here: the BEM and the closed form were compared on the testkit rows; no independent closed-form accuracy number is claimed beyond the cases in section 5.
- Embedded-microstrip exponent: two candidates (2 and 1.55) were scored against the BEM on a 12-case grid (`tests/test_embedded_exponent.py`): mean $|\Delta Z_0|$ 1.2 % and 1.1 %, worst 2.4 % and 2.9 %. The default is 1.55.
- Quasi-static assumption: not measured against a full-wave or measured reference in this repo. The cross-solver case `em.openems.il_10ghz` compares openEMS to the BEM RLGC cascade, see section 5.

**Numerical error.**

- BEM against exact solutions: up to 0.23 % (CPW half-space, caused by the 20x truncation, which is why benchmark B-IMP-01 currently fails its 0.05 % gate) and up to 0.054 % on thin Cohn stripline (section 5).
- BEM against FEM: Z0 within 0.43 %, Zdiff within 0.35 % on 50 structures. eps_eff on narrow, high-eps_r microstrip: the BEM is 0.57 to 0.59 % lower than the FEM, which sits nearer Hammerstad-Jensen. The BEM converges upward with panel density and the FEM from above, so the true value probably lies between; the cause in the BEM dielectric-interface panelling is not yet found (FOLLOWUPS, WP-202).
- FEM against exact: coax 0.02 %, wire over plane 0.004 %, two-wire 0.09 %, Cohn and coupled Cohn 0.001 %, CPW 0.11 % (README, 28 fixtures, all within 0.11 %).
- FEM R(f): round wire against the Bessel solution within 0.19 % from 1 kHz to 10 GHz; high-frequency asymptote against the BEM surface-impedance R within 5 % at 1, 5 and 10 GHz on a 35 µm stripline.

**Input error.** Not quantified by this package. Dk, Df, etch, laminate height and roughness are user inputs; their tolerances are the largest term in a real design. This is the target of the planned uncertainty quantification ([ADR-0012](../../docs/adr/0012-uncertainty-quantification.md), status Proposed).

## 5. Validation evidence

Source: `docs/validation/results.json`. "Independent" means the reference was not produced by this project's own code. All of these are static and, for the BEM, exact-geometry cases.

| Suite (case dir under `tests/golden/`) | Reference class | Cases | Tolerance | Worst error |
|---|---|---|---|---|
| `impedance_bem/cohn_thin_stripline` | analytic (Cohn 1954), true thin strip $t=10^{-6}b$ | 20 | 0.3 % | 0.054 % |
| `impedance_bem/cohn_thin_edge_coupled` | analytic (Cohn 1955) | 9 (Zdiff 6, Zcomm 3) | 0.3 % | 0.059 % (Zdiff), 0.038 % (Zcomm) |
| `impedance_bem/cpw_halfspace` | analytic (conformal map) | 4 Z0 | 0.3 % | 0.232 % |
| `oa-testkit` xsections (coax, wire over plane, two-wire) | analytic | 10 | 0.1 to 0.3 % per case | 0.124 % |
| `crosssolver/bem_vs_fem_50_structures` | cross-solver BEM (mesh x4) vs FEM | Z0 25, Zdiff 25, eps_eff 50 | 0.5 %, 1 %, 0.5 % | 0.425 %, 0.345 %, eps_eff 0.584 % (1 known gap; WP-202 accepted 0.7 %) |
| `crosssolver/bem_vs_fem_testkit` | cross-solver | Z0 20, Zdiff 4 | 0.5 %, 1 % | 0.234 %, 0.157 % |
| `impedance_closedform` HJ/Wheeler table | self-computed (formula lock at 1e-6 %) | 14 | - | regression only, not counted as validation |
| IPC-2141 legacy; Wheeler 1977 report value | published (IPC-2141) / self-computed (Wheeler value quoted in `docs/research`) | 2 | 0.1 ohm | both pass; only the IPC row counts as published |
| Pozar Ex. 3.6, Bryant-Weiss tables, Wadell examples, Polar SI9000, TNT-MMTL/atlc | published [verify] | 8 slots | - | **empty or unverified**; excluded from pass counts |

Cross-solver EM check (`em.openems.*`): openEMS FDTD on a 23 mm testkit microstrip against the BEM RLGC cascade gives IL 0.689 dB against 0.778 dB at 10 GHz (gate 0.2 dB, pass) and Z0 50.73 against 50.95 ohm thin-strip (3 % gate, pass). Against the 35 µm trapezoid BEM value (48.72 ohm) openEMS is 4 % off, because openEMS models the copper as a zero-thickness sheet (known gap, FOLLOWUPS WP-205).

**Known gaps and open issues (stated plainly).**

- Only 100 of 248 passing validation cases have an independent reference (FOLLOWUPS, WP-302); for impedance, the independent evidence is the analytic suites above. The seven Hammerstad-Jensen fixture rows are self-computed.
- Benchmark B-IMP-01 is **fail**: CPW half-space 0.2317 % against a 0.05 % gate (truncation; WP-510 owns the fix). B-IMP-02 and B-IMP-03 are `ref-pending`: the converged FEM references (24 SI9000-class structures, eddy-current R(f)) are not produced yet, so the BEM loss model (R(f), alpha) has only the round-wire FEM check and the 35 µm stripline asymptote.
- The Huray golden case checks the equation with typical values, not a digitised published curve. The cannonball-Huray tile area (36 r^2) is an unverified placeholder that may overestimate K (FOLLOWUPS, WP-106). Cannonball geometry is from secondary sources only.
- No closed form for broadside-coupled lines; no dispersion for coupled lines or CPW.
- The FEM has no element-wise adaptivity, no dispersion or anisotropy, and is slow (1 to 20 s per static solve, 5 to 25 s per eddy frequency).
- Via delay is not part of the profile. `ProfileEvent.kind` lacks some cause literals (they map to `other`).
- `oa_results` has no `ResultItem.accuracy` model yet; accuracy is carried by `accuracy_class` (`screening` for closed forms, `validated` for BEM, `reference` for a converged FEM) plus a `ValidityFlag(code="accuracy")`.

**Code-versus-documentation observations.**

- `_physics_compat.py` still describes itself as a shim "until `oa_physics` lands", although `oa-physics` is merged. Dielectric and roughness code is therefore duplicated. The two copies already differ in one place: for a tabulated Dk/Df (`TableDkDf`) the shim interpolates linearly in frequency, `oa_physics.eps_complex` interpolates in $\ln f$.
- Static requests (`f = 0`) evaluate dispersive dielectrics at the datasheet frequency (shim `eps_complex`), so a "static" result for a Djordjevic-Sarkar material is the value at `f_ref`, not at DC.

## 6. References

- E. Hammerstad, O. Jensen, "Accurate models for microstrip computer-aided design", *IEEE MTT-S Int. Microwave Symp. Dig.*, 1980, pp. 407-409.
- H. A. Wheeler, "Transmission-line properties of a strip on a dielectric sheet on a plane", *IEEE Trans. Microwave Theory Tech.* 25(8), 1977, pp. 631-647.
- S. B. Cohn, "Characteristic impedance of the shielded-strip transmission line", *IRE Trans. MTT* 2(2), 1954, pp. 52-57 [verify pages].
- S. B. Cohn, "Shielded coupled-strip transmission line", *IRE Trans. MTT* 3(5), 1955, pp. 29-38 [verify pages].
- M. Kirschning, R. H. Jansen, "Accurate model for effective dielectric constant of microstrip with validity up to millimetre-wave frequencies", *Electron. Lett.* 18(6), 1982, pp. 272-273 [verify pages].
- R. H. Jansen, M. Kirschning, "Arguments and an accurate model for the power-current formulation of microstrip characteristic impedance", *AEU* 37, 1983, pp. 108-112.
- M. Kirschning, R. H. Jansen, "Accurate wide-range design equations for the frequency-dependent characteristic of parallel coupled microstrip lines", *IEEE Trans. MTT* 32(1), 1984, pp. 83-90 [verify; errata 1985].
- B. C. Wadell, *Transmission Line Design Handbook*, Artech House, 1991.
- D. M. Pozar, *Microwave Engineering*, 4th ed., Wiley, 2012.
- IPC-2141A, *Design Guide for High-Speed Controlled Impedance Circuit Boards*, 2004 (the code cites the 1996 legacy formula as "IPC-2141").
- J. C. Rautio, "An ultra-high precision benchmark for validation of planar electromagnetic analyses", *IEEE Trans. MTT* (cited in BENCHMARKS.md B-EM-02 [verify details]).
- Project research: `docs/research/02-impedance.md`; change requests `docs/change-requests/WP-105-*.md`, `WP-202-*.md`.
