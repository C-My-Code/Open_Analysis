# 04 - Power Integrity (DC IR Drop and AC PDN)

Status: research report, 2026-10-27. Scope: power integrity (PI) for Open_Analysis. Data ingest, impedance, SI and 3D full-wave are covered in separate reports. This one only looks at where PI touches them.

Commercial reference points: Ansys SIwave (DC IR, PI Advisor, resonance), Cadence Sigrity PowerDC / PowerSI / OptimizePI, Siemens HyperLynx PI (DC drop, decoupling, plane noise), Keysight PIPro.

---

## 0. Executive summary

* **DC IR drop is a solved, tractable problem.** It is a 2D steady-current (Laplace) problem per copper layer, with sheet conductance `t/rho`, coupled through via-barrel resistances. P1 FEM (or FVM on a raster) plus a sparse SPD solve (CHOLMOD, PARDISO, or PyAMG+CG) handles real boards (10^5 to 10^7 unknowns) in seconds to a minute. The hard parts are geometry fidelity and contact modelling: pads, thermal reliefs, the zone fill state and pin groups. The numerics are the easy part.
* **AC PDN can reuse the same mesh.** Each plane-pair cavity obeys a 2D Helmholtz equation, `div(grad V) + k^2 V = -j*omega*mu*h*J`. If you assemble it on the DC triangulation, add via and port couplings, then reduce to a port Z-matrix (VRM, decaps, loads), decap selection and placement can be optimized as cheap circuit terminations without re-solving the field.
* **The open-source field moved quickly in 2025-2026.** Several KiCad PI tools exist: **padne** (GPL-3.0, FEM DC), **KiPI** (GPL-3.0, alpha, DC + AC + noise, the most complete), **Ki-PIDA** (AGPL-3.0, FDM DC), **kicad-irdrop** (Apache-2.0, raster DC, cross-checked against padne) and **kisi** (GPL-3.0). All are young. None ships a published validation suite against measurement. All are useful as **independent cross-check solvers**, and the copyleft ones can't be vendored into a permissively licensed project.
* **Recommendation:** Phase 1 is a DC IR-drop MVP (P1 FEM on gmsh or Triangle-free meshes, scipy/PyAMG, pin-group contacts, power plus ground return, heatmaps and via-current tables). Phase 2 is a lumped AC PDN with target impedance and a capacitor library. Phase 3 is a 2D-FEM cavity solver with port reduction. Phase 4 is decap optimization and time-domain droop. Phase 5 is return-path and stitching checks shared with SI.

---

## 1. DC IR drop

### 1.1 Physics and formulation

Copper features are thin compared with their lateral extent, so each layer is modelled as a 2D conducting sheet with **sheet resistance**

```
R_s(T) = rho(T) / t                   [ohm/sq]
rho(T) = rho_0 * (1 + alpha*(T - T_0))
rho_0  = 1.724e-8 ohm*m @ 20 C (IACS annealed; electro-deposited copper is often 1.7-1.9e-8)
alpha  = 3.93e-3 /K
```

Example: 1 oz copper (t = 35 um) gives R_s = 0.49 mOhm/sq at 20 C. Plated outer layers are thicker than the foil weight, so the stackup thickness should be used, not the nominal value.

Within one layer, with the potential `V(x, y)`, sheet current density `K = -grad(V)/R_s` [A/m] and injected current per area `s`:

```
-div( (1/R_s) * grad V ) = s          on Omega_layer (copper polygons with holes)
 n . grad V = 0                       on free copper edges (insulating boundary)
 V = V_src                            on source contacts (Dirichlet), or
 integral of K.n over the contact = I_load   on load contacts (Neumann / pin group)
```

The volume current density reported to users is `J = K / t` [A/m^2]. The usual design check uses A/mm^2 or the IPC-2152 derived limits.

**Layer coupling.** A via barrel between layers i and j is a lumped resistor:

```
R_via = rho * L / (pi * (r_o^2 - r_i^2)),    r_i = r_o - t_plating
```

Example: a 0.30 mm drill with 25 um plating and L = 1.6 mm has A = 0.0216 mm^2, so R_via ≈ 1.27 mOhm. A filled via or microvia uses `pi*r_o^2`. Each barrel is split into segments between every layer it connects on, so current can enter or leave at intermediate layers. Buried and blind spans follow the drill span. Barrel-to-copper connection: tie the barrel node to **all mesh nodes on the annular ring / pad perimeter** (as kicad-irdrop does), not to a single point. A point contact has a log-singular spreading resistance, so the result would depend on the mesh. Thermal-relief spokes must be meshed as actual geometry. They often dominate via resistance into a pour.

**Global system.** Assemble all layers of the net and the via resistors into one nodal conductance matrix `G V = I` (modified nodal analysis, MNA). `G` is symmetric positive definite once at least one Dirichlet node exists. Floating islands (copper with no source) must be detected (connected components on the graph) and removed or reported. Otherwise `G` is singular.

**Discretization options.**

| Method | How | Pros | Cons | Used by |
|---|---|---|---|---|
| P1 triangular FEM | Element stiffness `K_e = (A_e/R_s) * B^T B` (B = gradients of linear shape functions) | Exact polygons and holes, local refinement near vias/pads, standard | Needs a quality mesher, slivers from KiCad fills | padne, KiPI (Triangle) |
| Raster / FDM / FVM resistor grid | Cells of size h, conductance between neighbours `= 1/R_s` (square cells) | Trivial, robust to messy geometry, no mesher licence issue | Staircase edges, uniform h (memory), poor narrow-trace resolution unless h is tiny | Ki-PIDA, kicad-irdrop |
| Quadtree FVM | Adaptive raster | Middle ground | Hanging-node fluxes, more code | (none found) |
| Contour integral / BEM | Boundary-only per plane | Very fast for large planes | Complex for many holes and traces | TUHH research (Duan, Brüns, Schuster 2013) |

**Recommendation:** P1 FEM on a constrained Delaunay mesh, with the raster solver kept as a ~300-line independent reference implementation for testing. Traces should still be meshed as 2D polygons. KiCad tracks are rectangles with round caps. A 1D-resistor shortcut for long traces (`R = R_s * L / W`) is an optional speed-up.

### 1.2 Meshing arbitrary polygons with holes

Pipeline:

1. Extract copper per (net, layer) from the KiCad board (zone *filled* polygons, tracks, arcs, pads, vias). This is shared with the ingest report. **Zones must be refilled** before export, otherwise the result is wrong.
2. Union and clean with **Shapely 2.x** (BSD-3, GEOS): `unary_union`, `buffer(0)`, `simplify(tol≈1-5 um)`, sliver removal. Snap via/pad contact rings as polygon holes or embedded edges so mesh nodes lie on them.
3. Mesh:
   * **gmsh** (GPL-2.0-or-later with a linking exception; pip `gmsh`). It has a Python API, size fields (distance-to-via/pad refinement), and robust handling of thousands of holes. Recommended default.
   * **Triangle** (J. R. Shewchuk), via `triangle` / `meshpy`. Excellent and fast, but its licence is **not OSI open source**: commercial use needs permission. KiPI notes this explicitly. Avoid it as a hard dependency.
   * **Netgen** (LGPL-2.1) and **MMG** (LGPL-3) are alternatives if gmsh's GPL is a concern. CGAL is GPL or commercial.
   * The raster fallback needs no mesher.
4. Size control: target edge length h_max on planes ≈ 0.5-1 mm, h ≈ trace width / 3 on traces, and h ≈ ring circumference / 16 around via rings and pads. Graded growth of 1.2-1.3.

### 1.3 Sparse solvers

* `scipy.sparse.linalg.spsolve` (SuperLU, BSD). Fine up to ~5x10^5 unknowns.
* **CHOLMOD** via `scikit-sparse` (LGPL/GPL modules) or **PARDISO** via `pypardiso` (Intel MKL, multithreaded). KiPI reports a 645k-unknown solve dropping from about 8 s to 1.2 s. A direct factorization is reusable for many load cases and for the thermal Picard iterations.
* **PyAMG** (MIT) smoothed-aggregation AMG as a preconditioner for `scipy.sparse.linalg.cg`. O(N) memory, scales to 10^7 unknowns. Conductance contrast (vias are ~10^3-10^4 times stiffer than sheet elements) is handled well by AMG but hurts plain ILU.
* Strategy: direct solve below ~1-2 M unknowns, otherwise AMG-PCG with a relative tolerance of 1e-10 (the drops are mV on a V scale).

### 1.4 Sources, sinks and the power tree (UX)

* **VRM / source:** Dirichlet `V = V_set` on output pin group(s). Optional output resistance (Thevenin). Optional **remote sense**: regulate `V(sense+) - V(sense-) = V_set` with an extra unknown (MNA voltage source between the output and the sense node).
* **Load:** a component reference plus a pin set plus current `I` (with min/typ/max). Two contact models:
  * *Equal split* (simple, conservative for some pins, wrong for others).
  * *Pin-group equipotential* (recommended default): all pins of the group connect through a tiny resistor (e.g. 1 mOhm, as padne does) or an exact constraint to one lumped node that sinks `I`. The current split then follows from the copper. This matches how a BGA die really draws current, to first order. Per-pin currents are reported.
* **Ground return:** always solve the return net(s) as well. The reported "drop at load" is `(V_pwr,load - V_gnd,load)` vs `V_set`. Ground drop is often 30-50 % of the total on 2-4 layer boards.
* **Series elements:** ferrite beads, sense resistors, load switches and inductors as two-terminal R between pin groups (coupling nets).
* **Multi-rail trees:** solve leaf-to-root. A downstream regulator presents a load on its input rail of `I_in = V_out * I_out / (eta * V_in)` (switcher) or `I_in = I_out + I_q` (LDO). KiPI and Ki-PIDA both do this.
* **Defining it:** a per-project YAML/JSON setup file versioned next to the board (KiPI uses `<board>.kipi-setup.yml`, Ki-PIDA uses `kipida_config.json`, padne uses `!padne` directives in schematic text). Recommended: a schema-validated YAML (VRMs, loads, series parts, probes, limits) that the GUI and CLI both edit. Auto-populate candidates from the netlist (regulators by symbol/footprint keywords, large-pin-count ICs on a rail).

### 1.5 Outputs

* Per-layer voltage heatmap (absolute and drop relative to V_set), per-layer current-density magnitude with arrows/streamlines, and power-density `p = R_s |K|^2` [W/m^2].
* A table per load: V_min / V_avg at the pins, the margin vs tolerance, and PASS/FAIL.
* A table per via: current, current density, and the rating check (IPC-2152 or a user limit). Sort by worst first. Typical hotspots are via fields near VRMs and narrow necks in pours.
* Rail summary: effective R source-to-load, total copper loss `sum(I^2 R)`, and the worst neck.
* Exports: VTU (ParaView), CSV and an HTML report.

### 1.6 Thermal coupling

* **Level 0:** a user-set uniform board temperature, applied to `rho(T)`.
* **Level 1 (recommended Phase 1.5):** a 2D in-plane board conduction model on the same mesh. Use a lumped stackup conductivity `k_eff * t_board` (copper layers dominate in-plane), Joule source `q = sum over layers of R_s |K|^2`, and convective loss on both faces `h_conv * (T - T_amb)` (h ≈ 5-15 W/m^2K natural convection). Component power can be added as area sources. Iterate with Picard: solve V, compute q, solve T, update `R_s(T)`, repeat. Convergence takes 3-6 iterations. A reused Cholesky factorization of the thermal matrix makes this cheap.
* **Level 2:** a 3D stackup thermal model. Defer it, or hand it to a dedicated thermal module.

---

## 2. AC PDN impedance

### 2.1 Target impedance methodology

From Smith et al. (1999):

```
Z_target = (V_dd * ripple_fraction) / delta_I_transient
```

Example: 1.0 V with 3 % ripple and 10 A steps gives 3 mOhm. `delta_I` is often taken as 50 % of maximum current when only I_max is known.

* A flat target is valid from DC (handled by the VRM loop) to the frequency where the package and on-die capacitance take over (board-level PDN usually matters from ~10 kHz to ~50-200 MHz).
* Critiques and refinements: a flat target over-designs when the load current spectrum is narrow. Rogue-wave or resonance excitation can exceed it. See Smith and Bogatin (2017) and Novak's FDTIM / "Big-V" papers. Support **custom target masks** (piecewise in frequency) and a **time-domain droop check** (convolve the load current profile with Z(f) through IFFT).
* Data needed from the user: V_nom, tolerance, transient step, edge rate (which sets the upper frequency, `f_max ≈ 0.35 / t_rise`), and optionally a vendor PDN target (Xilinx UG583 and Intel PDN tool style).

### 2.2 Component models

**Capacitor (MLCC, polymer, bulk):**

```
Z_C(w) = ESR + j*w*ESL + 1/(j*w*C)
SRF = 1 / (2*pi*sqrt(ESL*C)),   Q at SRF = sqrt(ESL/C)/ESR
```

* Ceramic Class II capacitance must be **derated for DC bias, temperature and aging**. Derating can be 50-80 % at rated voltage for small cases.
* **Vendor models:**
  * Murata **SimSurfing**: S-parameters (Touchstone) and SPICE netlists at a user-specified DC bias and temperature. The "dynamic" SPICE models include bias dependence. https://article.murata.com/article/mlcc-dynamic-model-supports-circuit-simulations
  * KEMET **K-SIM**: S2P and SPICE outputs, plus C vs bias, temperature and ripple, for ceramic, tantalum, polymer, film and aluminium types. https://www.mouser.com/kemet-ksim-simulation-tool/
  * TDK, Samsung EM, Taiyo Yuden and Würth provide similar tools.
  * Licensing: vendor models are usually free to download but **not redistributable in bulk**. Ship a parametric "representative" library and let users import Touchstone (via **scikit-rf**, BSD-3) or SPICE subcircuits (parse simple RLC ladders directly, or evaluate an AC sweep with **ngspice** in shared-library mode).
  * Vendor S2P files are measured in a **series-thru or shunt fixture** with a specific mounting. Convert to a 1-port shunt impedance (`Z = 50 * S21 / (2 * (1 - S21))` for shunt-thru) and **de-embed or re-add** the mounting inductance consistently, otherwise the ESL is double-counted.

**Mounting inductance (pad + trace + vias down to the cavity):** the largest single lever on high-frequency decap performance.

* Via pair (barrel radius r, centre spacing s, height h above the plane pair): `L_vp ≈ (mu_0 * h / pi) * ln(s / r)` for s >> r. Example: h = 1 mm, s = 1 mm, r = 0.15 mm gives ≈ 0.76 nH.
* N parallel via pairs reduce it by about 1/N (less with mutual coupling).
* Add the trace from pad to via (≈ 0.5-1 nH/mm for thin traces over a plane at small h, or use a microstrip L' estimate) and the capacitor body ESL (≈ 0.3-1 nH depending on case and terminals; reverse-geometry and 3-terminal parts are lower).
* A physics-based via model (cavity + via barrel per Fan et al. and Schuster's group) generalizes this inside the cavity solver.

**VRM:** Smith's 4-element model gives a flat `R_out` at low frequency rising inductively above the loop bandwidth f_bw:

```
Z_VRM ≈ R_out + j*w*L_out,     L_out ≈ R_out / (2*pi*f_bw)
```

Add a series R/L for the output inductor path if needed. Users enter f_bw (from the datasheet or a loop simulation) and R_out (≈ load-line or droop setting, or ΔV/ΔI). KiPI instead treats the VRM as open-loop above f_bw. Supporting both is cheap.

**Load / die:** port impedance at the BGA or pin group. Optionally add a package R-L and die capacitance `C_die` (from vendor PDN tools) so the board-package resonance is visible.

### 2.3 Plane-pair (cavity) models

A plane pair with separation h, permittivity eps_r, loss tangent tan_d and conductor surface resistance obeys (for h much smaller than the wavelength, TM_z modes only):

```
div(grad V) + k^2 V = -j*w*mu*h*J_z
k^2 = w^2 * mu * eps_0 * eps_r * (1 - j*(tan_d + delta_s/h))       (lossy approx; delta_s = sqrt(2*rho/(w*mu_0)))
static capacitance C = eps_0*eps_r*A/h,   sheet inductance L_sq = mu_0*h  (= 31.9 pH/sq for h = 25.4 um)
resonances (rectangle a x b):  f_mn = c / (2*sqrt(eps_r)) * sqrt((m/a)^2 + (n/b)^2)
```

**(a) Analytic cavity model**, for rectangular planes with open (PMC) edges (Lei, Techentin, Gilbert, IEEE T-MTT 1999; Okoshi 1985). Mutual impedance between rectangular ports i and j (centres x, y and widths W_x, W_y):

```
Z_ij(w) = (j*w*mu*h / (a*b)) * sum_{m,n} [ chi_m^2 * chi_n^2 / (k_m^2 + k_n^2 - k^2) ]
          * cos(k_m x_i) cos(k_n y_i) * sinc(k_m W_xi / 2) sinc(k_n W_yi / 2)
          * cos(k_m x_j) cos(k_n y_j) * sinc(k_m W_xj / 2) sinc(k_n W_yj / 2)
k_m = m*pi/a,  k_n = n*pi/b,  chi_0 = 1, chi_{m>0} = sqrt(2),  sinc(x) = sin(x)/x
```

* Converges slowly in one index, so use the closed-form single-sum acceleration (Xu, Ji, Hubing, Drewniak, Missouri S&T: https://scholarsmine.mst.edu/faculty_work/247/).
* The (0,0) term gives `1/(j*w*C)`, and the remaining modes give the port spreading inductance plus resonances.
* Use it as the **reference solution for validating** the numerical cavity solver, and as a fast "plane-only" estimator.
* More background: https://cecas.clemson.edu/cvel/pdf/EMCS00-077.pdf

**(b) Segmentation**: the planar circuit is split into rectangles or triangles whose Z-matrices are known analytically, then connected through boundary ports (Okoshi). Combination formula for segments joined at internal ports q (from segment A) and r (from segment B), with external ports p:

```
Z_pp = Z_pp^A - Z_pq^A (Z_qq^A + Z_rr^B)^-1 Z_qp^A   (block form; generalizes to many segments)
```

Good for L- and T-shaped planes. Awkward for KiCad pours with hundreds of holes.

**(c) Transmission-plane / unit-cell (TMM, MFDM)**: each mesh cell becomes an RLGC cell (`L = mu_0*h`, `C = eps*A_cell/h`, `G = w*C*tan_d`, `R = 2*R_s(f)`). This is the same as a 2D FD/FVM discretization of the Helmholtz equation. Used by Na, Choi, Swaminathan et al. (IEEE T-AdvP 2000) and by Kim and Swaminathan's transmission matrix method (2001-2002). It handles arbitrary shapes and maps directly to SPICE.

**(d) 2D FEM Helmholtz (recommended).** Assemble `(K - k^2 M) V = -j*w*mu*h*I_ports` per cavity on the **same triangulation as DC**, where K is the P1 stiffness and M the P1 mass matrix.
* Losses go into complex k. Frequency-dependent conductor loss can be lumped.
* **Multiple cavities** (several plane pairs in a stackup) couple through via barrels. Each via is a node shared between cavities, with a barrel-segment inductance `L ≈ (mu_0 * t / 2*pi) * ln(r_antipad / r_via)` per plane traversal, as in the physics-based via model. A plane shared between two cavities carries currents on both faces.
* **Port reduction:** solve for the N_p ports, which are the VRM, every decap site and every load pin group. This takes N_p sparse RHS per frequency, with one factorization per frequency. 200-400 log-spaced frequencies from 10 kHz to 1 GHz. Model-order reduction (PRIMA / modal) can cut this further.
* **Termination (for optimization):** with port sets L (loads), C (decap and VRM sites), and Z_term = diag(Z_cap(w), Z_vrm(w)):

```
Z_L,eff = Z_LL - Z_LC (Z_CC + Z_term)^-1 Z_CL
```

  This lets a decap optimizer evaluate thousands of BOM/placement candidates as small dense solves. It is the core of Sigrity OptimizePI-style flows.
* **Validity:** the 2D cavity assumption (h much smaller than the wavelength, no radiation, no edge fringing) is fine for board PDN to ~1-2 GHz. Edge radiation and fringing add a small effective-size correction. Above that, hand off to full-wave (openEMS/Palace, covered in another report).

**(e) "PEEC-lite" / lumped approach (Phase 2 starting point).**
* Below the first plane resonance, the plane is a capacitor C_plane plus a **spreading inductance** between a decap location and the load.
* For a radial spread between concentric radii a (via cluster) and b: `L_spread ≈ (mu_0*h/(2*pi)) * ln(b/a)`.
* Between two via ports of radius r at distance d in a large plane pair: `L ≈ (mu_0*h/pi) * ln(d/r)`, the same form as the via pair.
* The Intel/Altera PDN Tool, Xilinx UG583 and KiPI-adjacent calculators use this lumped ladder: VRM, then bulk, then ceramics with mounting L, then spreading L, then package, then die.
* It gives correct first-order Z(f) and target crossover with no field solve. The **"board crossover"** is the frequency where the spreading-plus-mounting inductance alone exceeds Z_target. Above it, adding more board caps cannot help.
* Tomer-Harari/pdn-toolkit (MIT) implements this model.

### 2.4 Decap selection and placement optimization

* **Objective:** `max over f in [f1, f2] of |Z_L,eff(f)| / Z_target(f) <= 1`. Then minimize cost (BOM count, part count, area), optionally penalizing anti-resonance peaks.
* **Variables:** a part number per candidate site (including "empty"), from a library of derated models.
* **Algorithms:**
  * Greedy by "impedance reduction per cost" followed by pruning (pdn-toolkit's approach, deterministic).
  * Genetic algorithm / PSO for placement (common in the literature; e.g. Hubing's decap location studies and Fan et al. "Quantifying SMT decoupling capacitor placement", IEEE T-EMC 2001).
  * Integer programming over a discretized frequency set.
  * All are cheap once the Z-matrix is reduced.
* **Sensitivity:** `d|Z_L|/d(Z_term,k)` from the same algebra. This ranks which site matters, which is useful UX ("this cap does nothing above 20 MHz").

### 2.5 Ground bounce / SSN basics (scope note)

* Ground bounce: `V_gb = L_eff * dI/dt`. For N simultaneously switching drivers sharing a return inductance, `V ≈ N * L_ret * dI/dt`, which is dominated by package and via inductance.
* On the board, the PI tool contributes the **plane-pair transfer impedance** `Z_21(f)` between the aggressor (switching IC or switcher) and the victim location. This also covers switcher noise coupling into sensitive rails, as KiPI's "Coupled Noise" mode does.
* Time domain: `v(t) = IFFT{ Z_21(f) * I_aggr(f) }`, with a causality-checked, passivity-enforced Z(f). Use scikit-rf vector fitting for a SPICE-able macromodel.

---

## 3. Plane resonance, return-path discontinuities, stitching

* **Resonance map:** a Z11 magnitude map over the board at each cavity resonance f_mn (eigenmodes of `K v = k^2 M v` via `scipy.sparse.linalg.eigsh` with shift-invert). Show it as a heatmap with decap and via markers. This identifies "hot" locations where a stitching via or decap kills the mode. Commercial tools: SIwave resonance mode, HyperLynx plane noise.
* **Return-path discontinuity detection** (shared with SI). For every signal segment on layer i, find its reference plane(s) (nearest adjacent planes in the stackup) and intersect the segment's **footprint buffered by ~3h** with the reference-net copper polygons (Shapely). Flag:
  1. Crossing a split or void (the reference polygon is interrupted under the trace).
  2. Running within ~h of a plane edge.
  3. A layer change through a via where the reference net changes (GND to PWR, or a different GND layer) with no stitching via or stitching cap within a distance d_max. d_max is a rule, e.g. < 1/20 of the wavelength at the signal's knee frequency `f_knee ≈ 0.35 / t_rise` (λ = c / (f*sqrt(eps_eff))), with a sensible floor/ceiling in mm.
  4. Long reference changes through a cavity, where the return current must jump planes through displacement current. That cavity's Z at the knee frequency gives a quantitative severity: `V_noise ≈ I_return * Z_cavity(f)`.
  * Output a list of violations with a location, a severity and a suggested fix (add a stitching via or cap within X mm).
* **Via stitching:** check GND-to-GND via spacing along board edges, around RF regions and between ground pours.
  * Rule: spacing < λ/20 (strict) to λ/10 at f_max of concern. Example: 6 GHz with eps_r = 4.2 gives λ ≈ 24 mm, so λ/20 ≈ 1.2 mm.
  * Detect unstitched ground islands (copper on a GND net with < N vias, or with via spacing > rule) and floating copper (antenna risk).
  * The DC solver's connected-component pass already identifies disconnected islands.

---

## 4. Existing open-source tools and code

Repository metadata was taken from the GitHub API on 2026-10-27 (licence and last push).

### 4.1 KiCad / PCB PI tools

| Project | Licence | Last push | Stars | Scope | Method / notes |
|---|---|---|---|---|---|
| **padne**, https://github.com/atx/padne | GPL-3.0 | 2026-10-19 | 44 | DC IR drop | "2.5D FEM Laplace solver". Sources and loads via `!padne` directives in the schematic (VOLTAGE, CURRENT, RESISTANCE, REGULATOR, COPPER). Qt GUI and VTU export. C++ deps (Boost/MPFR/GMP, CGAL-like geometry). Docs at https://padne.atx.name. Forum: https://forum.kicad.info/t/presenting-padne-a-tool-for-dc-power-delivery-network-analysis/64966 |
| **KiPI**, https://github.com/drewsum/KiPI | GPL-3.0 (author states it may not be included in commercial software) | 2026-10-28 | 71 | DC + AC PDN + coupled noise + optional electro-thermal | Triangle-meshed FEM, SciPy or PARDISO. YAML setup. VRMs, loads, series links, caps (RLC/SPICE, derating). Target calculator per Smith 1999. Field-solved plane/via inductance. Imports ODB++, HyperLynx .hyp/.bud. 3D VTK views. HTML/PDF report. v0.9.4 alpha. **The most feature-complete open reference.** |
| **Ki-PIDA**, https://github.com/kbralten/KiPIDA | AGPL-3.0 | 2026-03-02 | 56 | DC IR drop + basic electro-thermal | Raster 2.5D FDM resistor grid. SciPy SuperLU/CG. Leaf-to-root multi-rail. wxPython UI, KiCad 9 IPC API. AC planned. |
| **kicad-irdrop**, https://github.com/firedanceio/kicad-irdrop | Apache-2.0 | 2026-10-20 | new | DC IR drop | Raster grid, via barrel chains tied to the full annular ring, sense points, per-via current vs IPC-2221, loss maps, CI exit codes. **Cross-checked against padne: 3.04 vs 2.97 mOhm (≈2.5 %)**. Write-up: https://www.hackster.io/gregory-gushard/kicad-irdrop-ir-drop-analysis-on-real-kicad-copper-36871e. Permissive, so its test board is reusable. |
| **kisi**, https://github.com/riehlies/kisi | GPL-3.0 | 2026-10-27 | 2 | SI + PI checks, browser/CLI | Early. |
| KiCad-Electrodynamic-Static-Studio, https://github.com/ravid1323/KiCad-Electrodynamic-Static-Studio | none stated | 2026-10-14 | 0 | DC MNA (C++) + openEMS AC | Experimental. No licence means not reusable. |
| kicad-electrothermal-sim (krosa-laboratory) | ? | - | - | DC IR + electro-thermal | Appeared in search results. The GitHub API returned 404 (moved or private). |
| pdn-toolkit, https://github.com/Tomer-Harari/pdn-toolkit | MIT | 2026-08-19 | 0 | Lumped PDN + greedy decap optimizer | Closed-form tests (SRF, crossover). Representative cap library. |
| deca_auto, https://github.com/YAEemf/deca_auto | MIT | 2025-10-10 | 6 | Decap combination search | SPICE .mod or C/ESR/ESL input. Development moved to a GUI repo. |
| GT-CHIPS/PDN-Impedance-Analysis, https://github.com/GT-CHIPS/PDN-Impedance-Analysis | none | 2018 | 10 | MATLAB PDN Z | Academic, stale. |
| gerber2ems (Antmicro), https://github.com/antmicro/gerber2ems | Apache-2.0 | 2026-10-16 | 275 | openEMS from Gerbers (SI) | Useful pattern for full-wave PDN cross-checks. |

Takeaways:

* Feature parity with KiPI for DC + lumped AC is a realistic target.
* The differentiators would be:
  1. A permissive or clean licence stack (no Triangle).
  2. A published validation suite, which none of the tools has against measurement.
  3. A 2D-FEM multi-cavity solver with Z-matrix port reduction and optimization.
  4. Integration with the suite's SI return-path checks.
* We can **run** the GPL tools in CI as external cross-check oracles (process boundary) without licence contamination. **Do not copy code** from GPL/AGPL projects into Open_Analysis unless Open_Analysis itself is GPL-compatible.

### 4.2 Numerical libraries

| Library | Licence | Role | Status |
|---|---|---|---|
| Shapely 2 (GEOS) | BSD-3 | Polygon union, cleanup, queries | Very active |
| gmsh, https://gmsh.info | GPL-2.0+ with linking exceptions | 2D constrained meshing, size fields, Python API | Very active |
| Triangle | Non-OSI (no commercial use without permission) | Fast 2D quality mesher | Stable. Licence risk. |
| Netgen / MMG | LGPL | Alternative meshers | Active |
| scikit-fem, https://github.com/kinnala/scikit-fem | BSD-3 | Pure-Python P1/P2 assembly (`asm(laplace, basis)`, mass matrices), ideal for the DC and Helmholtz assembly | Active (pushed 2026-10-27, 671 stars) |
| FEniCSx / dolfinx, https://github.com/FEniCS/dolfinx | LGPL-3.0 | Heavy-duty FEM, PETSc, complex mode | Active. Heavier install (conda), overkill for 2D. |
| Elmer FEM, https://github.com/ElmerCSC/elmerfem | GPL-2 (solver) / LGPL (library) | StatCurrentSolve, heat, 3D thermal | Active. Useful as an external validation for 3D via/thermal. |
| SciPy sparse | BSD-3 | SuperLU, CG, eigsh | Core |
| PyAMG, https://github.com/pyamg/pyamg | MIT | AMG preconditioner | Active |
| pypardiso | BSD (MKL proprietary but redistributable) | Fast multithreaded direct solver | Optional |
| scikit-sparse (CHOLMOD) | GPL/LGPL modules | Cholesky | Optional |
| scikit-rf, https://github.com/scikit-rf/scikit-rf | BSD-3 | Touchstone I/O, network algebra, vector fitting | Active |
| ngspice (shared lib) | BSD-3 / mixed | Evaluating vendor SPICE cap models | Active |
| openEMS, https://github.com/thliebig/openEMS | GPL-3.0 | FDTD cross-check of plane pairs, vias and decaps | Active |
| Palace, https://github.com/awslabs/palace | Apache-2.0 | 3D FEM frequency-domain, PDN cross-check above 1 GHz | Active |
| PyVista / VTK | MIT / BSD-3 | 2D/3D field visualization, VTU export | Active |

### 4.3 Literature, app notes and validation references

* L. D. Smith, R. E. Anderson, D. W. Forehand, T. J. Pelc, T. Roy, "Power distribution system design methodology and capacitor selection for modern CMOS technology", *IEEE Trans. Adv. Packaging*, 22(3), 284-291, Aug 1999. This is the origin of target impedance and the VRM model. Catalogue record: https://vufind.lboro.ac.uk/PrimoRecord/cdi_proquest_miscellaneous_28522050
* L. D. Smith, E. Bogatin, *Principles of Power Integrity for PDN Design - Simplified*, Prentice Hall, 2017. Covers lumped PDN, spreading inductance, rogue waves and FDTIM. Larry Smith columns: https://signalintegrityjournal.com/authors/213-larry-smith
* I. Novak, J. Miller, *Frequency-Domain Characterization of Power Distribution Networks*, Artech House, 2007. Covers measurement methods, the 2-port shunt-thru method and capacitor characterization. Novak columns: https://www.signalintegrityjournal.com/authors/23-istvan-novak
* Novak et al., "Impact of Finite Interconnect Impedance Including Spatial and Domain Comparison of PDN Characterization", DesignCon 2024 Best Paper. Includes measured benchmark-board data. https://www.signalintegrityjournal.com/articles/3723-impact-of-finite-interconnect-impedance-including-spatial-and-domain-comparison-of-pdn-characterization
* **Samtec / IEEE EPS TC-EDMS open PI benchmark board** (high-current, low-impedance PDN). Design files in Allegro and IPC-2581: https://blog.samtec.com/new-open-source-ieee-eps-tc-edms-packaging-benchmark/ and slides https://suddendocs.samtec.com/notesandwhitepapers/samtec-ppt-new-power-integrity-benchmark-for-low-impedance-structures.pdf. Suite: http://packaging-benchmarks.org/. **This is the best available public measured PI validation case.** The IPC-2581 import must be supported, or the board converted.
* TU Hamburg SI/PI-Database (multi-cavity via arrays, simulated, for ML): https://www.tet.tuhh.de/?p=4983. Schierholz et al., IEEE Access 2021. A good reference for the multi-cavity via coupling model.
* G.-T. Lei, R. W. Techentin, B. K. Gilbert, "High-frequency characterization of power/ground-plane structures", *IEEE Trans. MTT*, 47(5), 1999. Analytic cavity Z_ij.
* T. Okoshi, *Planar Circuits for Microwaves and Lightwaves*, Springer, 1985. Segmentation and contour integral.
* Closed-form cavity input impedance (Xu, Ji, Hubing, Drewniak): https://scholarsmine.mst.edu/faculty_work/247/ and the Clemson CVEL papers https://cecas.clemson.edu/cvel/pdf/EMCS00-077.pdf, https://cecas.clemson.edu/cvel/pdf/zur01-96O2.pdf
* J. Kim, M. Swaminathan, transmission matrix method for power planes (IEEE T-AdvP 2001/2002). Na, Choi, Swaminathan et al., unit-cell plane models (IEEE T-AdvP 2000). M. Swaminathan, A. E. Engin, *Power Integrity Modeling and Design for Semiconductors and Systems*, Prentice Hall, 2007.
* J. Fan, J. Drewniak et al., "Quantifying SMT decoupling capacitor placement in dc power-bus design for multilayer PCBs", IEEE T-EMC 43(4), 2001. Also the physics-based via/cavity circuit models (Fan, Missouri S&T; Schuster group, TUHH).
* X. Duan, H.-D. Brüns, C. Schuster, contour integral method for DC resistance of power planes, 2013: https://tore.tuhh.de/handle/11420/6702. A. Rimolo-Donadio et al., hybrid cavity / radial waveguide approach for lossy planes, 2009: https://orion.tec.ac.cr/en/publications/hybrid-approach-for-efficient-calculation-of-the-parallel-plate-i/
* Cadence PowerSI plane-impedance extraction walkthrough (commercial reference workflow): https://resources.pcb.cadence.com/view-all/impedance-characterization-of-pcb-power-ground-planes-using-extraction-mode-of-powersi-part-1
* Analog Devices, "Simulating Power Planes with LTspice" (unit-cell ladder, a good simple cross-check): https://www.analog.com/cn/resources/technical-articles/simulating-power-planes-with-ltspice.html
* Vendor PDN references: Xilinx/AMD UG583 PCB design guide (PDN decap tables and targets), the Intel FPGA PDN Tool (lumped spreadsheet with spreading inductance), Murata SimSurfing and KEMET K-SIM (links above).

---

## 5. Recommendation

### 5.1 Phased roadmap

**Phase 0 - shared foundations (with the ingest team).**
* Per-net, per-layer copper polygons (filled zones, tracks and arcs, pads with thermal reliefs, via spans with drill and plating), stackup (thicknesses, eps_r, tan_d, copper thickness), component pins and pin-to-net mapping.
* A canonical `CopperModel` object with Shapely geometry and a stable ID scheme.
* A connectivity graph to detect islands.

**Phase 1 - DC IR drop MVP.**
* One rail plus its ground return. P1 FEM on gmsh meshes, assembled with scikit-fem or a hand-written vectorized assembler (trivial for P1).
* Via barrel segments tied to ring nodes. Pin-group loads (equipotential), VRM Dirichlet with optional remote sense.
* Solver: CHOLMOD or PARDISO when available, otherwise SuperLU, with AMG-PCG above ~1 M unknowns.
* Outputs: per-layer V and |J| heatmaps, a via current table, a load PASS/FAIL table, a VTU and an HTML report.
* CLI plus a YAML setup file. A KiCad IPC plugin wrapper later.
* Raster FVM reference solver for tests.
* Acceptance: within 1 % of analytic cases, and within 3 % of padne and kicad-irdrop on shared boards.

**Phase 1.5 - production DC.**
* Multi-rail power tree (leaf to root, efficiency).
* Series elements (beads, shunts, switches).
* Temperature-dependent rho plus the 2D Joule-heating Picard loop.
* Current-density and via-current DRC limits.
* Batch and corner runs (min/typ/max currents).
* Incremental re-solve (cache the factorization when only loads change).

**Phase 2 - AC PDN, lumped.**
* Target-impedance calculator and custom masks.
* Capacitor library: parametric RLC with DC-bias derating curves, plus import of Touchstone (scikit-rf) and SPICE (RLC parse or ngspice).
* VRM R/L model. Mounting-inductance estimator from actual pad, trace and via geometry. Spreading inductance from the plane geometry (`mu_0*h` sheet inductance, ln-ratio formulas).
* Z(f) at each load vs target. Board crossover frequency.
* Analytic rectangular cavity Z_ij for simple boards.

**Phase 3 - AC PDN, field-solved.**
* 2D FEM Helmholtz per cavity on the DC mesh, with complex k losses.
* Multi-cavity coupling through via models.
* Port reduction to a Z-matrix over 10 kHz-1 GHz. Resonance eigenmodes and maps.
* Transfer impedance between arbitrary points (noise coupling).
* Touchstone export of the board PDN for use in SPICE or SI channel tools.

**Phase 4 - optimization and time domain.**
* Decap selection and placement optimizer on the reduced Z-matrix (greedy + prune, then GA), with cost and BOM objectives and sensitivity ranking.
* Time-domain droop from a load current profile (IFFT, vector-fitted macromodel).

**Phase 5 - SI crossover.**
* Return-path discontinuity checker, via stitching and ground-island checks, and cavity-noise severity for reference changes. Coordinate with the SI module.
* Optional full-wave cross-checks via openEMS or Palace, handled by the 3D team.

### 5.2 Numerical approach and libraries (summary)

* Geometry: Shapely 2. Meshing: gmsh (default), with an optional raster path that needs no mesher. **Avoid Triangle** as a hard dependency (licence).
* Assembly: scikit-fem or a custom NumPy P1 assembler (vectorized COO).
* Linear algebra: SciPy sparse plus PyAMG, with optional pypardiso or scikit-sparse.
* AC: complex sparse LU per frequency (SuperLU or PARDISO complex symmetric). scikit-rf for networks and vector fitting. ngspice for vendor SPICE models.
* Visualization: Matplotlib `tripcolor` / `tricontourf` for static reports, PyVista or VTK (or a WebGL viewer in the suite UI) for interactive 3D stackup views, Plotly for Z(f).
* Licence posture: everything above is BSD, MIT, LGPL or Apache, except gmsh (GPL with exception). If Open_Analysis is permissive, call gmsh as a separate process or optional plugin, or use Netgen (LGPL).

### 5.3 Inputs and UX for sources and loads

* **Setup file** (`<board>.oa-pi.yml`), schema-validated and versionable. The GUI and CLI both read and write it.

  ```yaml
  rails:
    - name: VCC_1V0
      net: +1V0
      return: GND
      vrm: {ref: U5, pins: [9,10,11], vset: 1.000, sense: {pos: U1.AA12, neg: U1.AB12},
            rout: 0.5m, f_bw: 100k}
      loads:
        - {ref: U1, pins: auto, current: {typ: 12, max: 18}, model: pin_group, tol: 3%}
      series: []
      target: {ripple: 3%, step: 9A, f_max: 100M}       # AC
  thermal: {mode: picard, t_amb: 25, h_conv: 10}
  mesh: {h_plane: 0.8mm, h_trace: w/3, via_ring_segments: 16}
  ```

* Auto-detect regulators and high-current loads from the netlist and propose rails. Highlight the selected pins on the layout.
* Show **unsupported or ambiguous geometry** prominently: unfilled zones, islands, pads with no copper connection.
* Report a **mesh-convergence indicator** by re-solving at h/2 on demand and reporting the delta.

### 5.4 Results visualization

* DC:
  * Per-layer voltage-drop heatmap (sequential colormap, fixed to the rail's tolerance budget so red means violation) and |J| heatmap (log scale) with current arrows.
  * Via markers sized and coloured by current.
  * Power-density map, and a temperature map when thermal coupling is on.
  * Hover probe of the local V, J and layer.
  * Per-load and per-via sortable tables. Click to jump to the location.
* AC:
  * Log-log |Z(f)| per load with the target mask overlay, the contribution of each decap group, and the crossover and anti-resonance annotations.
  * Phase plot. Resonance-mode maps. Z-magnitude heatmap at a chosen frequency.
  * Decap "effectiveness" ranking.
* Reports: HTML with embedded PNG and SVG, VTU for ParaView, CSV, and Touchstone.

### 5.5 Validation cases (test suite)

**DC (analytic):**
1. Rectangular strip L x W with full-width end contacts: `R = R_s * L / W`. Error should be < 0.1 %.
2. Annulus with inner radius a and outer radius b: `R = R_s * ln(b/a) / (2*pi)`.
3. Two-layer plane joined by N vias: series and parallel via formula.
4. Point-contact convergence: a small round contact in a large disk, checking the log spreading term under mesh refinement.
5. Temperature: `R(T)` linear with alpha. Joule-heated strip with known h_conv against a 1D analytic fin solution.

**DC (cross-tool):** kicad-irdrop's 4-layer cross-check board (Apache-2.0; published 3.04 mOhm / 2.97 mOhm padne). KiPI and padne run as external oracles on a set of open KiCad boards (for example the KiCad demo projects and open-hardware boards with defined rails). Optionally Elmer StatCurrentSolve in 3D for via/pad crowding.

**AC (analytic and numerical):**
1. Rectangular plane pair: FEM Helmholtz vs the analytic cavity double sum (Lei/Okoshi), Z11 and Z21 at several ports, with resonances f_mn to < 1 %.
2. Low-frequency limit `1/(j*w*C_plane)` and spreading inductance vs the ln-formula.
3. Single cap SRF and ESR. Parallel identical caps (`Z/N`). Smith 1999's flat-impedance bank example.
4. Lumped ladder vs a SPICE (ngspice) netlist of the same.
5. openEMS or Palace full-wave of a plane pair with a via and decap, for > 500 MHz behaviour.
6. **Measured:** the Samtec / IEEE EPS TC-EDMS open PI benchmark board (Novak, DesignCon 2024), and the Smith/Bogatin and Novak book examples.

---

## 6. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Geometry fidelity (unfilled or stale zones, thermal reliefs, arcs, teardrops, pad shapes, net-ties) | Wrong answers that look plausible | Refuse unfilled zones. Mesh reliefs explicitly. Geometry regression tests against KiCad DRC copper. |
| Contact models (pin-group vs equal split, point singularities) | 5-30 % differences at loads and VRMs | Ring-node contacts, pin-group default, convergence indicator |
| Scale (large boards, 10+ layers, fine meshes) | Memory and time | Graded meshes, AMG, per-net solves, trace 1D shortcut, cached factorizations |
| Licences (Triangle non-free; GPL/AGPL tools; gmsh GPL) | Distribution constraints | Avoid Triangle. Run GPL tools out-of-process only. Decide the project licence early. |
| Vendor capacitor data (non-redistributable, fixture de-embedding, DC-bias derating) | Wrong high-frequency Z | User import, documented de-embedding, conservative default derating |
| VRM and load models are user-supplied and uncertain | Garbage in, garbage out | Templates from vendor PDN tools, corner runs, sensitivity display |
| Validity of the 2D cavity above ~1-2 GHz, edge radiation, package and die not modelled | Missed high-frequency resonances | State the limits in the UI. Package/die R-L-C port model. Hand off to the full-wave module. |
| Limited public **measured** validation data | Credibility vs commercial tools | Adopt the Samtec/TC-EDMS benchmark. Commission a small in-house test board (planes plus decap fields, VNA 2-port shunt-thru) early. |
| Competition and overlap with KiPI (already DC+AC) | Duplicated effort | Differentiate on licence, validation, multi-cavity FEM with port reduction and optimization, and SI integration. Use KiPI as an oracle. |
