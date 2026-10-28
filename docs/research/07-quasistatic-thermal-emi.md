# 07 — Quasi-static extraction, thermal, EMI/EMC, and remaining feature gaps

Status: research draft, 2026-10-27
Scope: the gaps left after the ingest, 2D impedance, SI channel, PI (DC IR and AC PDN), 3D full-wave, and product-landscape tracks:

1. 3D quasi-static RLC extraction (like Q3D)
2. PCB thermal analysis with electro-thermal coupling (like Icepak-lite, HyperLynx Thermal, Sigrity PowerDC)
3. EMI/EMC rule checks, radiated-emission estimates, and the near-to-far-field transform
4. Other SIwave, HyperLynx, and Sigrity features
5. Recommendations, phasing, interfaces, and validation

Project license constraint: Open_Analysis is GPL-3.0. MIT, BSD, Apache-2.0, LGPL-2.1+/3, and GPL-2.0-or-later code can be linked or bundled. GPL-2.0-only code cannot be linked. Any solver can still run as a separate process that reads and writes files, which avoids most licensing questions. Subprocess invocation is the default pattern below.

Items marked **[verify]** were not confirmed against a primary source during this research.

---

## 1. 3D quasi-static field extraction (Q3D-like)

### 1.1 What Q3D provides

Q3D Extractor computes these quantities for 3D conductor sets ("nets") with defined sources and sinks:

- Capacitance and conductance: the Maxwell C matrix, and G from the dielectric loss tangent.
- DC R and L: current flows through the whole conductor volume.
- AC R and L: current is limited to the skin depth. Q3D blends the DC and AC results into an R(f)/L(f) fit.

It exports SPICE subcircuits (lumped or distributed), IBIS-ISM, and Touchstone files (computed from the RLGC data), and it has a separate 2D-extractor mode. For PCBs it is mostly used on via fields, connector and pin-field footprints, package substrates (bond wires, balls), bus bars, and plane cutouts or necks.

### 1.2 Governing equations

**Electrostatics (C and G).** For conductors $i=1..N$ at potentials $V_j$, solve $\nabla\cdot(\varepsilon\nabla\phi)=0$ with $\phi=V_j$ on the conductor surfaces. The charge is $Q_i=\oint_{S_i}\varepsilon\,\partial\phi/\partial n\,dS$, and the Maxwell capacitance matrix satisfies $\mathbf Q=\mathbf C\mathbf V$, with $C_{ij}\le 0$ for $i\ne j$. The SPICE "mutual" capacitance between nets is $C^{m}_{ij}=-C_{ij}$, and the capacitance to ground is $C^{g}_{i}=\sum_j C_{ij}$.

In BEM form (FastCap/FasterCap), with homogeneous dielectric, conductor surface charge $\sigma$ satisfies

$$\phi(\mathbf r)=\int_S \frac{\sigma(\mathbf r')}{4\pi\varepsilon\,|\mathbf r-\mathbf r'|}\,dS' .$$

Multilayer dielectrics add free-space panels on the dielectric interfaces, which carry a polarization-charge condition $\varepsilon_a\partial_n\phi_a=\varepsilon_b\partial_n\phi_b$. FastCap accelerates the dense $\mathcal O(n^2)$ products with a fast multipole method (Nabors & White, 1991). FasterCap adds hierarchical (H-matrix-like) compression and automatic refinement.

Energy form, used by FEM codes such as Palace and Elmer: $C_{ii}=2W_e/V_i^2$, and the off-diagonal terms come from pairwise excitations.

Conductance from dielectric loss: $G = \omega\tan\delta\,C$ for a uniform dielectric. In a mixed stackup, scale each region's contribution, or solve with complex permittivity $\varepsilon(1-j\tan\delta)$.

**Partial inductance and PEEC (L and R).** Ruehli's partial inductance between volume cells $i$ and $j$ with cross-sections $a_i$, $a_j$ and uniform current is

$$L_{p,ij}=\frac{\mu_0}{4\pi\,a_i a_j}\int_{v_i}\int_{v_j}\frac{\hat{\mathbf l}_i\cdot\hat{\mathbf l}_j}{|\mathbf r-\mathbf r'|}\,dv_j\,dv_i ,\qquad R_i=\frac{l_i}{\sigma a_i}.$$

Loop inductance follows from the partial inductances: $L_{loop}=L_{p,11}+L_{p,22}-2L_{p,12}$, generalized as $\mathbf L_{loop}=\mathbf M^T\mathbf L_p\mathbf M$. This is why partial-element methods suit PCB return-path questions.

FastHenry discretizes conductors into filaments. It solves mesh (loop) equations $\mathbf M(\mathbf R+j\omega\mathbf L_p)\mathbf M^T\mathbf I_m=\mathbf V_s$ with a multipole-accelerated GMRES and returns the port impedance matrix $\mathbf Z(\omega)=\mathbf R(\omega)+j\omega\mathbf L(\omega)$ (Kamon, Tsuk & White, 1994). Frequency dependence comes from filament subdivision, which requires filaments smaller than the skin depth $\delta=\sqrt{2/(\omega\mu\sigma)}$. For copper, $\delta = 2.1\,\mu$m at 1 GHz and 66 µm at 1 MHz. FastHenry supports ground planes as meshed sheets, including holes ("hole point/rect/circle"). That makes it directly useful for plane cutouts and via fields above planes.

**Magnetostatic FEM** (Palace, Elmer): $\nabla\times(\nu\nabla\times\mathbf A)=\mathbf J$, with $\mathbf L_{ij}$ from field energy $W_m=\tfrac12\mathbf I^T\mathbf L\mathbf I$. This gives DC (volume-current) inductance only. AC R and L need a harmonic eddy-current solve: Elmer's `WhitneyAVHarmonicSolver` / MagnetoDynamics, or a low-frequency driven solve in Palace.

**Analytic sanity formulas** (useful for unit tests and fast estimates):

- Via barrel partial self-inductance (Goldfarb & Pucel, 1991): $L=\frac{\mu_0}{2\pi}\left[h\ln\frac{h+\sqrt{r^2+h^2}}{r}+\tfrac32\left(r-\sqrt{r^2+h^2}\right)\right]$
- Via pad capacitance (Johnson & Graham, *High-Speed Digital Design*): $C\approx\frac{1.41\,\varepsilon_r T D_1}{D_2-D_1}$ pF, with T, D1 (pad) and D2 (antipad) in inches
- Partial self-inductance of a round wire with $l\gg r$: $L\approx\frac{\mu_0 l}{2\pi}\left(\ln\frac{2l}{r}-\tfrac34\right)$
- Loop inductance of a via pair (signal + return) between planes separated by $h$, pitch $s$: $L\approx\frac{\mu_0 h}{\pi}\ln\frac{s}{r}$

### 1.3 Open-source candidates

| Tool | Method | License | Maintenance | Windows | Python route | Fit |
|---|---|---|---|---|---|---|
| **FastHenry2** (FastFieldSolvers Windows port of MIT FastHenry 3.0) | Multipole-accelerated PEEC, filaments, ground planes with holes; R(f), L(f) | Original MIT FastHenry is MIT-style (README.mit in the repo; FreshPorts lists MIT). The Windows port carries "a more permissive license, distributed with the sources" [verify file] | Low activity; bundle 5.2.0 | Yes, native, plus an automation server (COM) in the bundle | Write a `.inp` text file, run the CLI, parse `Zc.mat`. The FreeCAD EM Workbench already generates FastHenry input | **Primary** R(f)/L(f) for via arrays, plane necks, connectors, packages |
| **FastCap2** | FMM BEM, multi-dielectric | MIT-style original; FFS Windows port "more permissive" [verify] | Dormant | Yes | Write a `.lst`/`.qui` panel file, parse stdout | Legacy; superseded by FasterCap |
| **FasterCap** | Hierarchical/FMM BEM with auto-refinement; 2D and 3D; multi-dielectric | **LGPL** (stated on the FFS download page; AUR lists LGPL-2.1-or-later) | Low activity; v6.0.7 | Yes (GUI and CLI), plus Linux | CLI with a FastCap-compatible input file, parse the C matrix | **Primary** fast C matrix for 3D structures |
| **Palace** (AWS Labs) | High-order FEM (MFEM); `Electrostatic` → `terminal-C.csv`, `terminal-Cm.csv`; `Magnetostatic` → `terminal-M.csv` | Apache-2.0 | Very active | No native build; Linux/macOS via Spack. Use WSL2 or Docker on Windows | JSON config plus gmsh mesh; CSV out | **Accurate reference** C and L(DC); shares mesh and geometry code with the full-wave track |
| **Elmer FEM** | FEM. `StatElecSolver` with `Calculate Capacitance Matrix`; `StatCurrentSolver` (DC R); `MagnetoDynamics` / `WhitneyAVHarmonicSolver` (AC R, L) | ElmerSolver LGPL-2.1; GUI and most of the rest GPL-2-or-later [verify] | Active (CSC Finland) | Yes, official Windows installers | Generate a `.sif` text file plus mesh via `ElmerGrid`/gmsh, run `ElmerSolver` | Good all-rounder; the same tool covers thermal (Section 2) |
| **bempp-cl** | Python BEM (Laplace, Helmholtz, Maxwell); Numba/OpenCL | MIT | Moderately active (v0.4.x) | Yes (pip) | Native Python | Pure-Python C-matrix extractor; needs our own multilayer Green's function or interface panels |
| **scikit-fem** | Pure-Python FEM | BSD-3 | Active | Yes (pip) | Native | Small 2D/3D electrostatic and thermal problems; easy to embed and test |
| **FEniCSx (DOLFINx)** | FEM | LGPL-3 | Very active | conda-forge Windows builds [verify]; WSL is safest | Native Python | Heavier dependency; justified only if we also need it elsewhere |
| **VoxHenry** (MIT) / **FastImp** (MIT) | Voxel FFT inductance (MATLAB); surface integral impedance | Research licenses [verify] | Dormant | MATLAB | — | Reference only |
| **gmsh** | Mesher (OCC kernel) | GPL-2-or-later with linking exception | Very active | Yes (pip wheel) | `gmsh` Python API | **Required** meshing for all FEM routes |

Sources: FastFieldSolvers downloads and licenses (https://www.fastfieldsolvers.com/dwnld02.htm, https://www.fastfieldsolvers.com/software.htm, https://www.fastfieldsolvers.com/git_repository_content.htm); repos https://github.com/ediloren/FasterCap, https://github.com/ediloren/FastHenry2, https://github.com/ediloren/FastCap2; FastHenry 3.0 mirror https://github.com/hohe/fasthenry-3.0wr; FreshPorts https://freshports.org/science/fasthenry/; AUR https://aur.archlinux.org/packages/fastercap; FreeCAD EM Workbench https://wiki.freecad.org/EM_Workbench; Palace docs https://awslabs.github.io/palace/stable/ and its rings inductance example https://awslabs.github.io/palace/stable/examples/rings/; Elmer capacitance via FreeCAD https://wiki.freecad.org/FEM_EquationElectrostatic; bempp-cl https://github.com/bempp/bempp-cl and JOSS https://joss.theoj.org/papers/10.21105/joss.02879.

Key papers:

- K. Nabors, J. White, "FastCap: a multipole accelerated 3-D capacitance extraction program," IEEE TCAD 10(11), 1991.
- M. Kamon, M. Tsuk, J. White, "FASTHENRY: a multipole-accelerated 3-D inductance extraction program," IEEE TMTT 42(9), 1994.
- A. Ruehli, "Equivalent circuit models for three-dimensional multiconductor systems," IEEE TMTT 22(3), 1974.
- A. Ruehli, G. Antonini, L. Jiang, *Circuit Oriented Electromagnetic Modeling Using the PEEC Techniques*, Wiley-IEEE, 2017.

### 1.4 In-house PEEC option

A purpose-built quasi-static PEEC in NumPy/SciPy is feasible for PCB geometry. Rectangular bars, sheets, and cylinders have closed-form partial inductances (Ruehli; Hoer & Love, 1965, for rectangular bars). Capacitance would use panel potential coefficients. With H-matrix compression (e.g. `h2tools`, or our own ACA), a few thousand cells is easy.

Advantages: no external binaries, MIT-clean code, direct access to current distributions (which feed the EMI far-field in Section 3), and a natural link to the SI netlist.

Disadvantages: multilayer dielectric capacitance needs either layered Green's functions (complex) or interface panels.

Recommendation: wrap FastHenry2 and FasterCap first, then grow an in-house PEEC for the **inductance and resistance** part (Phase 3+). Inductance is where homogeneous-medium methods are exact ($\mu=\mu_0$ everywhere), so a pure-Python solver is both accurate and easy to verify there.

### 1.5 From matrices to SPICE and RLGC

1. **Lumped π/T subcircuit per net group.** Split each net's C into ground and mutual parts. Put R and L in series with K couplings $k_{ij}=L_{ij}/\sqrt{L_{ii}L_{jj}}$, or use SPICE `K` statements over all pairs. Half the C goes at each end (π model). Before emitting the subcircuit, check passivity: $\mathbf C$ must be diagonally dominant with a non-positive off-diagonal, and $\mathbf L$ and $\mathbf R$ must be symmetric positive definite. Fix small negative eigenvalues by projection.
2. **Frequency-dependent RL.** Fit $Z(\omega)=R(\omega)+j\omega L(\omega)$ from a FastHenry sweep:
   - (a) A ladder network (Kim & Neikirk, "Compact equivalent circuit model for the skin effect," IEEE MTT-S 1996): series R0–L0 plus parallel $R_k\|L_k$ sections. It is passive by construction.
   - (b) Vector fitting (Gustavsen & Semlyen, 1999), with passivity enforcement, then synthesis to an RL-foster network. The `scikit-rf` `VectorFitting` class (BSD-3) can export SPICE subcircuits directly.
3. **Distributed and RLGC.** For a uniform-cross-section 3D section of length $\ell$, per-unit-length values are $\mathbf L'=\mathbf L/\ell$ and $\mathbf C'=\mathbf C/\ell$. These can be fed to the SI engine's W-element or tabular RLGC model. Only do this when end effects are small. Otherwise emit a cascade of N π sections with $N\ge 10\,\ell/\lambda_{min}$.
4. **S-parameters.** Convert the RLGC/subcircuit to Touchstone with the SI engine's netlist simulator, or analytically for 2-port π sections, so the channel tool can cascade it. State the validity limit in the output: quasi-static is good while the structure is $\lesssim\lambda/10$.

### 1.6 Typical PCB workloads and expected solver choice

| Structure | Quantity | Default solver | Reference solver |
|---|---|---|---|
| Single/differential via with antipads | C (pad/barrel), L loop, stub | FasterCap + FastHenry2 | Palace electrostatic/magnetostatic; openEMS for S-params |
| Via array / BGA pin field | C, L matrices, return-via sensitivity | FastHenry2 (planes with holes) | Palace |
| Connector footprint (press-fit) | C, L, k | FasterCap / FastHenry2 | Palace |
| Plane neck, cutout, slot | R(DC), L(f) of current path | FastHenry2 (planes with holes) | Elmer harmonic AV |
| Bond wire / package-ish | R(f), L(f), mutual | FastHenry2 | — |
| Decap mounting loop (pads + vias + planes) | L_mount | FastHenry2 | Palace |

The last row feeds the PI track: mounting inductance is the dominant decap parasitic above about 10 MHz.

---

## 2. PCB thermal analysis

### 2.1 Target capability (HyperLynx Thermal / Sigrity PowerDC electro-thermal / Icepak-lite)

- Board-level steady state, with an optional transient later.
- Component power maps, with compact models for junction temperature.
- Copper-dependent anisotropic conductivity.
- Convection and radiation boundaries.
- Optional heatsink or chassis as fixed temperature or $h$.
- Joule heating from the DC-IR solver, with temperature-dependent copper resistivity (electro-thermal iteration).

### 2.2 Equations

Steady conduction with a volumetric source:

$$\nabla\cdot(\mathbf k\,\nabla T)+q'''=0,\qquad \mathbf k=\mathrm{diag}(k_{xy},k_{xy},k_z).$$

Surface boundary (Robin) condition with linearized radiation:

$$-\mathbf n\cdot\mathbf k\nabla T=h_c(T-T_\infty)+\varepsilon\sigma_{SB}(T^4-T_\infty^4)\approx(h_c+h_r)(T-T_\infty),\quad h_r=4\varepsilon\sigma_{SB}T_m^3$$

At $T_m\approx 330$ K with $\varepsilon=0.9$ (solder mask), $h_r\approx 7.5$ W/m²K, comparable to natural convection.

Natural convection (Incropera; $Ra_L=g\beta\Delta T L^3/(\nu\alpha)$):

- Vertical plate: $\overline{Nu}=0.59Ra^{1/4}$
- Hot surface facing up: $0.54Ra^{1/4}$ ($10^4<Ra<10^7$)
- Hot surface facing down: $0.27Ra^{1/4}$
- Simplified for air: $h\approx1.42(\Delta T/L)^{1/4}$ W/m²K for a vertical plate
- Typical PCB range: $h_c\approx 5$–$15$ W/m²K in still air, and $20$–$100$ W/m²K forced. A forced-air flat-plate correlation is $\overline{Nu}=0.664Re^{1/2}Pr^{1/3}$ (laminar).

Effective layer conductivity. For a copper fill fraction $\phi$ in a layer cell:

- In plane (parallel mixing): $k_{xy}=\phi k_{Cu}+(1-\phi)k_d$
- Through plane (series): $k_z = \left(\phi/k_{Cu}+(1-\phi)/k_d\right)^{-1}$, often $\approx k_d$. A better choice for discrete copper is the Maxwell–Garnett / Lemczyk-type estimate.
- Stack-averaged: $k_{xy}=\sum k_i t_i/\sum t_i$ and $k_z=\sum t_i/\sum(t_i/k_i)$
- Vias in a cell of area $A_c$: $k_z^{eff}=k_d+N_v\,k_{Cu}\,\pi\left(r_o^2-r_i^2\right)/A_c$, using the plating ring and adding fill material if the via is filled

Typical values: $k_{Cu}\approx 390$ W/m·K, and FR-4 $k_{xy}\approx0.8$, $k_z\approx0.3$. The bulk anisotropy of FR-4 glass weave is itself about 2–3×.

Joule heating and electro-thermal coupling:

$$\nabla\cdot(\sigma(T)\nabla V)=0,\quad q'''=\sigma(T)|\nabla V|^2=|\mathbf J|^2/\sigma,\quad \rho(T)=\rho_0[1+\alpha(T-T_0)],\ \alpha_{Cu}=3.93\times10^{-3}/\mathrm K.$$

Iterate with Picard updates and under-relaxation: solve V on the current T, compute $q$, solve T, update σ, and repeat until $\max|\Delta T|<0.1$ K. Convergence usually takes 3–6 iterations. Thermal runaway (no convergence) is a real failure mode and should be reported, not hidden.

Component models, in increasing fidelity:

- (a) Uniform power over the courtyard or pad area.
- (b) Two-resistor model (θJC, θJB) per JESD15-3: junction node → case → ambient (via $h$), and junction → board (pads).
- (c) DELPHI multi-resistor compact model (JESD15-4).
- (d) Detailed package from a vendor.

Datasheet θJA values are measured on JEDEC boards (JESD51-3 1s, JESD51-7 2s2p) in a JESD51-2A still-air chamber. They are **not** transferable to the user's board, so the tool should use θJC/θJB with the board model instead.

### 2.3 Solver approaches

| Approach | Description | Pros | Cons |
|---|---|---|---|
| **A. Layered FVM ("2.5D")** | One 2D grid per copper layer and per dielectric (or lumped dielectric), with vertical conductances between them and via enhancement per cell. Sparse SPD solve (SciPy CG with AMG via `pyamg`, MIT). This is the HyperLynx Thermal / PowerDC style | Fast (≈10⁶ unknowns in seconds), pure Python, reuses the DC-IR grid and rasterization, trivially couples to DC IR on the same mesh | Ignores 3D spreading inside thick parts; components are only compact models |
| **B. 3D FEM with Elmer `HeatSolver`** | Tetrahedral or extruded prism mesh from gmsh, anisotropic `Heat Conductivity` tensor, `Heat Transfer Coefficient` BCs; can couple `StatCurrentSolver` for Joule heat in one `.sif` | Proven, Windows binaries, true 3D; handles heatsinks and enclosures | Mesh generation of a full board is heavy; external process |
| **C. scikit-fem / FEniCSx** | FEM in Python | Easy to embed, good for reference sub-problems | Mesh-size limits for scikit-fem (pure Python assembly), deployment weight for FEniCSx |
| **D. CFD (OpenFOAM `chtMultiRegionFoam`)** | Conjugate heat transfer with airflow | Real Icepak parity | Very heavy; Phase 5+ and optional |

Prior art: **KiCad Thermal Sim** (github.com/poealex/kicad_thermal_sim, MIT, created 2026). It is a KiCad plugin using layered 2D FV per copper layer with vertical coupling, matrix-free PCG with multigrid, uniform $h$ on both faces, vias as enhanced vertical coupling, pad power or PWL sources, and DC Joule heating. Its README states it has **no validation** against measurements. It is a useful reference implementation, and its MIT license lets us borrow ideas or code. Sources: https://github.com/poealex/kicad_thermal_sim, https://awesome.ecosyste.ms/api/v1/projects/github.com%2Fpoealex%2Fkicad_thermal_sim. FreeCAD's `fcad_pcb` (https://github.com/realthunder/fcad_pcb) builds solid copper for FEM.

### 2.4 Validation data

- **JEDEC JESD51 series** (https://www.jedec.org/node/7480; overview https://www.electronics-cooling.com/2019/11/jedec-thermal-standards-developing-a-common-understanding/):
  - JESD51-3 low-k 1s board
  - JESD51-7 high-k 2s2p board: 4 layers, 2 oz outer, 1 oz inner planes. A commonly cited size is 114.3 × 76.2 × 1.6 mm [verify the exact figure in the standard]
  - JESD51-2A still-air chamber
  - JESD51-5 thermal-pad variants

  Benchmark: model a 2s2p board with a 5×5 mm source at 1 W and compare θJA trends with published values for common packages, e.g. TI SPRA953 "Semiconductor and IC Package Thermal Metrics" (https://www.ti.com/lit/an/spra953c/spra953c.pdf) and the JEDEC-board θJA tables in TI/ADI/Richtek app notes (Richtek AN044: https://www.richtek.com/~/media/AN%20PDF/AN044_EN.pdf; ADI: https://www.analog.com/en/resources/technical-articles/thermal-characterization-of-ic-packages.html).
- **IPC-2152** trace-heating charts (current vs ΔT vs cross-section, with and without planes). These are an excellent electro-thermal coupled test, e.g. 10 °C rise curves.
- **Analytic:**
  - 1D slab
  - Fin equation for a trace: $T-T_\infty\propto e^{-x/\lambda}$ with $\lambda=\sqrt{k t/(2h)}$
  - Spreading resistance of a circular source on a plate (Lee, Song, Au, Moran, "Constriction/spreading resistance model for electronics packaging," ASME/JSME 1995; Yovanovich)
- **App notes:** TI SLMA002 PowerPAD thermal via arrays (https://www.ti.com/lit/an/slma002h/slma002h.pdf); TI SNVA419 "AN-2020 Thermal design by insight, not hindsight" (https://www.ti.com/lit/an/snva419c/snva419c.pdf). These give board-area-vs-θJA curves suitable for regression.
- **Cross-solver:** layered FVM vs Elmer 3D on identical boards. Agreement within 5% on peak ΔT is the acceptance bar.

---

## 3. EMI/EMC

### 3.1 Rule-based EMC checks (HyperLynx DRC, Quiet Expert, CR8000 Lightning)

The Clemson CVEL PCB EMC expert-system work became Mentor Quiet Expert and Zuken CR5000 Lightning. It is the canonical rule source and frames rules by *radiated-emission impact*, not just geometry (https://cecas.clemson.edu/cvel/emc/expert_systems/PCB/pcb_es_overview.html, https://cecas.clemson.edu/cvel/Projects/cvel_proj_emi-exp.html, Hubing et al. EMC'97 https://cecas.clemson.edu/cvel/pdf/EMCS97-444.pdf). It is not open source, so the rules have to be re-implemented from the literature.

Each rule below lists its geometry needed and a parameterization. All are cheap computational-geometry queries using shapely/GEOS (BSD) on the shared layout model. Severity can be weighted by net edge rate: $f_{knee}\approx0.35/t_r$ or $0.5/t_r$.

| # | Rule | Check | Default threshold |
|---|---|---|---|
| E1 | Trace near board edge | Distance from high-speed segment to the board outline or plane edge | ≥ 3·h (dielectric height to reference) or ≥ 1 mm; also the plane-to-edge pullback |
| E2 | Reference plane gap / slot crossing | Project the trace onto its reference layer(s) from the stackup; intersect with plane voids (antipads merged, splits, slots) | Any crossing on nets with $t_r$ < threshold → error unless a stitching cap is within $d\le\lambda_{knee}/20$ |
| E3 | Unreferenced high-speed net | Segment over no copper on the adjacent plane layer (or the adjacent layer is not plane-assigned) | Any |
| E4 | Reference change without return via | Layer transition where the from/to references differ in net; search for a return via (GND-to-GND) or a cap (PWR-to-GND) within radius $r$ | $r\le$ 1–2 mm or $\lambda/20$ at $f_{knee}$ |
| E5 | Stitching via spacing | Max distance between GND vias along plane edges, guard traces, and shield cans; perimeter vias | $s\le\lambda/20=c/(20f_{max}\sqrt{\varepsilon_{r,eff}})$ (≈ 7.5 mm at 1 GHz in air; ≈ 3.6 mm at 1 GHz in εr = 4.3) |
| E6 | Decap coverage | For each IC power pin, find the nearest decap on the same rail by loop path (pin → via → plane → via → cap), or radius as a fallback | Radius ≤ $\lambda/20$ of the decap's useful band, or inductive budget (links to the PI track) |
| E7 | Clock/high-speed trace length on outer layers | Length on microstrip layers | Configurable |
| E8 | Parallelism / crosstalk | Coupled length and spacing (3W rule) between aggressor and victim classes | Spacing ≥ 3W, or $k_{NEXT}$ limit |
| E9 | I/O connector filtering | Nets entering a connector have a filter or ESD part within $d$ of the connector; chassis-GND bonding | Configurable |
| E10 | Split-plane / power island stitching | Each island is bridged by caps across splits where signals cross | — |
| E11 | Plane cavity resonance | $f_{mn}=\frac{c}{2\sqrt{\varepsilon_r}}\sqrt{(m/a)^2+(n/b)^2}$ for each plane pair (rectangular approximation, or an eigenmode solve) vs clock harmonics | Warn if a harmonic falls within ±5% of $f_{mn}$ and the plane pair is fed by a high-speed via |
| E12 | 20H edge rule (power planes inset 20·h from GND) | Offset check | Informational only: its benefit is disputed (Hubing, "the 20-H rule," UMR/Clemson studies). Do not mark as error |
| E13 | Long, unshielded I/O traces near high-speed areas | Proximity of I/O nets to clock nets/ICs (voltage-driven common-mode coupling) | Configurable |

Implementation note: E2/E3/E4 need **return-path inference**: reference layer by stackup adjacency, and reference net from planes or zones. The SI and PI tracks need the same inference, so it should live in the shared model as `ReferenceMap(net_segment → [(layer, net, coverage_fraction)])`.

### 3.2 Radiated emission estimates (closed-form)

Ott (*Electromagnetic Compatibility Engineering*, Wiley 2009, ch. 12) and Paul (*Introduction to EMC*, 2nd ed., ch. 8):

**Differential-mode (small loop)**, measured over a reflecting ground plane (Ott includes the +6 dB ground reflection):

$$E_{DM}\approx 263\times10^{-16}\,\frac{f^2 A\,I_s}{r}\ \ [\mathrm{V/m}]$$

where $f$ is in Hz, the loop area $A$ is in m², the current $I_s$ is in A, and the distance $r$ is in m. Paul's free-space equivalent is $1.316\times10^{-14}f^2AI/r$. Loop area comes from the trace length times its height above the return plane: $A = \ell\,h$ for a microstrip.

**Common-mode (short monopole/cable)**:

$$E_{CM}\approx 1.257\times10^{-6}\,\frac{f\,L\,I_{cm}}{r}\ \ [\mathrm{V/m}]\quad (L\ll\lambda)$$

Paul's free-space form is $6.28\times10^{-7}fLI/r$. For $L\gtrsim\lambda/4$, cap the result at the half-wave-dipole value $E\approx 60I/r$.

**Source spectrum.** The trapezoidal-pulse envelope has amplitude $2A\tau/T$ up to $f_1=1/(\pi\tau)$, falls at −20 dB/dec to $f_2=1/(\pi t_r)$, then at −40 dB/dec. The harmonic currents are $I_n = V_n/|Z_{line}(f_n)|$, taken from the SI engine (driver model plus line impedance), or better, from the simulated current waveform's FFT at the driver.

**Common-mode current estimation** follows Hockanson, Drewniak, Hubing et al., "Investigation of fundamental EMI source mechanisms driving common-mode radiation from printed circuit boards with attached cables," IEEE TEMC 38(4), 1996:

- *Current-driven* mechanism: $V_{CM}=j\omega L_{p,ret}I_{DM}$, where $L_{p,ret}$ is the return-plane partial inductance (from Section 1 PEEC or the closed form $L_{p,ret}\approx\frac{\mu_0 \ell h}{\pi w}$-type expressions)
- *Voltage-driven* mechanism: $V_{CM}\approx V_{trace}C_{trace}/C_{board}$
- Then $I_{CM}=V_{CM}/Z_{ant}$ with an attached-cable antenna impedance (≈ 50–300 Ω, or a monopole model)
- CVEL's "Maximum Radiated Emission Calculator" (Hubing group) uses this kind of model to bound the worst case

**Limits for overlay plots:**

- FCC Part 15 Class B at 3 m (quasi-peak): 40 / 43.5 / 46 / 54 dBµV/m for 30–88 / 88–216 / 216–960 / >960 MHz
- CISPR 32 Class B at 10 m: 30 dBµV/m (30–230 MHz) and 37 dBµV/m (230–1000 MHz)
- CISPR 32 Class B above 1 GHz at 3 m: 50 dBµV/m average and 70 dBµV/m peak, up to 3 GHz [verify]

Distance scaling: $1/r$ in the far field. Data: 47 CFR 15.109 (https://www.ecfr.gov/current/title-47/chapter-I/subchapter-A/part-15/subpart-B/section-15.109).

**Per-net report.** For each net class, give the worst-case $E$ at 3 m and 10 m, split into DM and CM, against the limit (margin in dB). This mirrors SIwave's "EMI Scanner" and HyperLynx "EMC" estimates. It is cheap and Phase 2-able.

### 3.3 Current-based far-field (between closed form and full wave)

The SI engine already computes $I(z,\omega)$ along each trace from its transmission-line solution: forward and backward waves, $I(z)=\frac{V^+}{Z_0}(e^{-\gamma z}-\Gamma_L e^{\gamma(z-2\ell)})$. Treat each segment as a Hertzian dipole at height $h$ above an infinite ground plane, using image theory: a horizontal dipole has an opposite-sign image at $-h$. Then sum:

$$\mathbf E(\mathbf r)=\sum_k \frac{j\omega\mu_0}{4\pi}\,I_k\,\Delta\mathbf l_k\,\frac{e^{-jk_0R_k}}{R_k}\Big|_{\perp}\ +\ \text{images}.$$

Vertical segments (vias) are included with a same-sign image. This reproduces the "TL-based radiation" method of Leone ("Radiated susceptibility on the printed-circuit-board," IEEE TEMC 2003) and Paul's multiconductor TL radiation models. It is accurate for electrically small board cross-sections and needs no 3D mesh. Board-edge and plane-resonance radiation is missing. Cover that with the cavity model: edge magnetic currents $\mathbf M=2\,\hat{\mathbf n}\times\mathbf E_z h$ on the plane perimeter, using $E_z=V_{plane}/h$ from the AC PDN solution (the PI track's cavity/plane solver). The PI track therefore needs to export the plane-voltage distribution $V(x,y,\omega)$.

### 3.4 Near-to-far-field transform (from full-wave)

For openEMS or Palace results, use the equivalence principle on a closed Huygens box $S$: $\mathbf J_s=\hat{\mathbf n}\times\mathbf H$ and $\mathbf M_s=-\hat{\mathbf n}\times\mathbf E$. The radiation vectors are

$$\mathbf N=\oint_S \mathbf J_s\,e^{jk\hat{\mathbf r}\cdot\mathbf r'}dS',\qquad \mathbf L=\oint_S \mathbf M_s\,e^{jk\hat{\mathbf r}\cdot\mathbf r'}dS',$$

$$E_\theta=-\frac{jke^{-jkr}}{4\pi r}(L_\phi+\eta N_\theta),\qquad E_\phi=\frac{jke^{-jkr}}{4\pi r}(L_\theta-\eta N_\phi).$$

Radiated power is $P_{rad}=\frac{1}{2\eta}\oint|E|^2r^2d\Omega$, and directivity is $D=4\pi U/P_{rad}$ (Balanis, *Antenna Theory*, 4th ed., ch. 6 and 12; Taflove & Hagness, ch. 8).

- **openEMS** already has `nf2ff` (GPL-3, Python `CalcNF2FF`), which works on frequency-domain dumps of a box. It is directly reusable, and the licenses are compatible.
- **Palace** has far-field postprocessing in recent releases [verify the version and API]. Otherwise, export surface fields and run our own transform. The transform is about 150 lines of NumPy and is a good unit test (Hertzian dipole, half-wave dipole).
- For EMC, report $E$ at 3 m and 10 m, and include the semi-anechoic chamber ground reflection with a two-ray model and receive-antenna height scan (1–4 m) to compare with measured data. A max-over-height bound is $|E_{direct}|+|E_{reflected}|$.

### 3.5 Validation cases (EMI)

1. Hertzian and half-wave dipole far field (analytic).
2. A microstrip over a finite ground plane driven at one end and terminated in a load. Compare the TL-dipole sum vs openEMS NF2FF vs measured data from Leone 2003 / Hockanson 1996.
3. The UMR/Clemson "EMC test boards" in IEEE TEMC papers on CM radiation (board plus attached cable): reproduce the trends.
4. A plane-pair cavity: resonance frequencies vs the analytic $f_{mn}$, and radiated field vs the cavity model.
5. Rule-check fixtures: synthetic KiCad boards, each violating exactly one rule (golden-file tests).

---

## 4. Other SIwave / HyperLynx / Sigrity features not covered elsewhere

| Feature (vendor) | What it does | Feasibility | Depends on |
|---|---|---|---|
| **SYZ extraction** (SIwave) | Multi-port S/Y/Z of a selected board region: hybrid 2.5D (TL for traces plus a cavity/plane solver plus via models), wideband, causal Touchstone output | Medium–high. Build from a cavity-model plane solver (PI track) + 2D TL (impedance track) + via models (Section 1) + network stitching. Full accuracy needs full wave on demand | PI AC solver, 2D solver, Section 1, scikit-rf |
| **Resonance/eigenmode scan** (SIwave) | Plane-pair modes with field plots | Easy (cavity eigen) to medium (Palace eigenmode) | PI track |
| **Crosstalk scan** (SIwave, HyperLynx BoardSim) | Screen all net pairs for coupled length and spacing → $K_{NEXT}$, $K_{FEXT}$ estimates; worst-aggressor lists | **Easy.** Geometry plus 2D coupled-line LC: $K_b=\frac14(\frac{L_m}{L}+\frac{C_m}{C})$, FEXT $\propto \frac{\ell}{2 t_r}(\frac{C_m}{C}-\frac{L_m}{L})$ | 2D solver |
| **Impedance and delay scan** (SIwave TDR scan, HyperLynx BoardSim "batch") | Per-segment Z0, delay, and reference continuity for every net; flags discontinuities | **Easy** once 2D tables exist | 2D solver, ReferenceMap |
| **Batch net screening** (HyperLynx BoardSim batch) | Auto-assign IBIS models, simulate every net with default stimulus, report overshoot, ringback, delay, monotonicity | Medium (orchestration on top of the SI engine; ngspice batch) | SI engine, IBIS parser |
| **DDR wizard** (HyperLynx, SIwave DDR, Sigrity SystemSI) | DDR3/4/5 and LPDDR: groups byte lanes, applies JEDEC timing/derating, eye masks, write leveling | Medium–high; mostly rules and timing tables plus batch simulation. Start with DDR4 static timing plus SI eyes | SI engine, IBIS, a timing-budget module |
| **SerDes/compliance** (HyperLynx SerDes, SIwave channel) | COM (IEEE 802.3), PCIe/USB channel compliance | Medium; COM reference MATLAB is public, Python ports exist [verify] | SI track |
| **Chip-package-board co-simulation** (Sigrity, SIwave CPA, Redhawk-like) | Package model (S-param/RLGC) + board + die (CPM) for PI/SI | Medium; it is netlist assembly. The hard part is data formats: CPM is proprietary, IBIS-ISM and IBIS package models are open | Netlist/Touchstone handling |
| **Package extraction** (Sigrity XtractIM, Q3D) | RLGC per pin of the package substrate | Medium; Section 1 tools + automation over pins | Section 1 |
| **Power-aware SI / SSN** (Sigrity, HyperLynx) | SI simulation with non-ideal PDN | Medium; co-simulate the PI PDN Z(f) model in the SI netlist | PI + SI |
| **Decap optimization** (Sigrity OptimizePI, HyperLynx PI) | Choose the decap set to meet target impedance at minimum cost | Medium; GA/greedy on the PI track's Z(f) model | PI track, BOM library |
| **Power tree / DC budget** (Sigrity PowerTree) | Topology of regulators → loads, efficiency, current budget | **Easy**; graph from the schematic or netlist | Ingest |
| **Electro-thermal DC IR** (PowerDC) | Section 2 | Covered here | — |
| **ESD / transient immunity** | Discharge path analysis, TVS placement rules | Easy (rules); hard (simulation) | Rules engine |
| **Near-field scan import** | Compare simulated near field with scanner data | Medium | Section 3.4 |
| **Lightning / immunity (BCI)** | Field-to-line coupling (Taylor/Agrawal) | Medium; TL-based | SI track |
| **Via stub and backdrill analysis** | Stub resonance $f=c/(4\ell\sqrt{\varepsilon_{eff}})$ + backdrill planning | **Easy** | Stackup |
| **Report generation / compliance docs** | HTML/PDF reports per analysis | Easy | — |

---

## 5. Recommendations

### 5.1 Choices per item

| Item | Recommendation | Fallback / reference |
|---|---|---|
| 3D C matrix | **FasterCap** subprocess wrapper (LGPL, Windows native). Geometry → panel file via our own mesher (quads from extruded polygons, cylinders for vias) | Palace `Electrostatic` in WSL/Docker for accuracy; bempp-cl for a pure-Python path |
| 3D R(f), L(f) | **FastHenry2** subprocess wrapper; uses its ground-plane-with-holes feature for planes | Palace `Magnetostatic` (DC L), Elmer AV-harmonic (AC); in-house PEEC (Phase 4) |
| Circuit export | scikit-rf vector fitting + passivity → SPICE subckt; Kim–Neikirk ladder for RL; π-cascade for distributed | — |
| Thermal (default) | **In-house layered FVM** (NumPy/SciPy + pyamg), sharing the DC-IR raster; Picard electro-thermal loop | Elmer `HeatSolver` + `StatCurrentSolver` 3D as reference/"high-fidelity" mode |
| EMC rules | **In-house** shapely-based rules engine, rules E1–E13, YAML-configurable thresholds | — |
| Radiated estimate | Closed-form DM/CM (Ott/Paul) per net with spectra from the SI engine; then TL-dipole sum + cavity edge radiation | openEMS full wave + NF2FF |
| NF2FF | Reuse openEMS `nf2ff`; own NumPy implementation for Palace or general surface data | — |
| Crosstalk/impedance scans | In-house, on top of the 2D solver tables | — |

### 5.2 Phasing (aligned with a suite-wide plan)

- **Phase 1 (foundations):** Shared layout model incl. `ReferenceMap`, the via geometry descriptor, and copper raster per layer. Analytic via R/L/C estimators (Section 1.2 formulas) as placeholders. EMC rules E1–E6 and E11. Impedance scan.
- **Phase 2:**
  - Thermal layered FVM + electro-thermal coupling with DC IR. Validate against analytic cases, IPC-2152, and JEDEC 2s2p trends.
  - Closed-form radiated estimates (DM/CM) with limit overlays.
  - Crosstalk scan.
  - FasterCap/FastHenry2 wrappers for vias and via arrays, with SPICE export.
- **Phase 3:**
  - Generalized 3D extraction regions (connectors, plane necks, decap mounting loops → feeds PI).
  - Frequency-dependent RL fitting with passivity.
  - TL-current far field + cavity edge radiation.
  - Batch net screening.
  - Elmer 3D thermal reference mode.
- **Phase 4:**
  - SYZ hybrid extraction.
  - In-house PEEC (inductance + current density for EMI).
  - NF2FF on full-wave runs with chamber model.
  - DDR wizard (DDR4 first).
  - Decap optimizer.
  - Package/board co-sim netlisting.
- **Phase 5 (optional):** transient thermal, CFD (OpenFOAM CHT), compliance (COM), immunity/BCI.

### 5.3 Interfaces to the shared layout data model

These are the minimal contracts the shared model should expose, independent of solver.

```python
# geometry/stackup (shared, produced by ingest)
Stackup.layers: list[Layer(name, kind={'cu','dielectric','mask'}, t, eps_r(f), tan_d(f),
                           sigma, k_xy, k_z, rho0, alpha_T, roughness)]
Board.outline: shapely.Polygon
Copper.on(layer) -> {net: MultiPolygon}           # merged zones+tracks+pads
Via(id, net, x, y, drill, plating_t, pad_per_layer, antipad_per_layer,
    span=(top_layer, bot_layer), backdrill, filled: material|None)
Net(name, class, pins, edge_rate_s, f_max)        # edge_rate from IBIS/user
ReferenceMap: segment_id -> [(layer, ref_net, coverage)]  # used by SI/PI/EMC
Component(ref, footprint_polygon, layer, power_W, theta_jc, theta_jb, pins)

# extraction regions (Section 1)
Region3D(bbox|polygon, layers, nets, ports=[Port(net, +terminal, -terminal)])
  -> Conductors3D (prisms from polygons, cylinders from vias)
  -> backends: FasterCapBackend, FastHenryBackend, PalaceES/MSBackend, ElmerBackend
ExtractionResult(C[n,n], G[n,n], R(f)[n,n], L(f)[n,n], freqs, port_map, validity_fmax)
  -> to_spice_subckt(), to_rlgc(length), to_touchstone(freqs)

# thermal (Section 2)
ThermalGrid(nx, ny, layer_stack) built from Copper raster (fill fraction per cell)
ThermalBC(h_top, h_bot, T_amb, emissivity, fixed_T_regions, heatsinks)
ThermalResult(T[layer,x,y], T_junction[component], heat_flux_paths)
couple(dc_ir_solver, thermal_solver, tol=0.1K) -> (V, J, T)

# EMI (Section 3)
EmcRule(id, params, applies_to=NetClassSelector) -> list[Violation(geom, severity, msg)]
CurrentSource: per-segment I(f) from SI engine; plane V(x,y,f) from PI engine
FarField(theta, phi, f) -> E; Emission(net, f, E_3m, E_10m, limit, margin_dB)
```

Cross-track dependencies:

- **SI track** provides per-net driver spectra and TL currents, and consumes via/connector subcircuits.
- **PI track** provides plane voltage maps and current density J for Joule heat, and consumes decap mounting L and $\rho(T)$ feedback.
- **2D track** provides coupled L/C per unit length for crosstalk scans.
- **Full-wave track** shares the gmsh geometry builder and Palace/openEMS runners, and provides surface fields for NF2FF.

### 5.4 Validation matrix

| Case | Reference | Acceptance |
|---|---|---|
| Two spheres / parallel plates C | Analytic; Palace "first simulation" two-sphere example; FreeCAD/Elmer two balls (C12 ≈ 1.693 in that example, units per the FreeCAD page) | < 2% |
| Concentric rings mutual L | Palace rings example (analytic) https://awslabs.github.io/palace/stable/examples/rings/ | < 2% |
| Round wire and rectangular bar L, R(f) | Analytic (Ruehli/Hoer–Love; Bessel-function skin effect for a round wire) | < 3%; R(f) trend matches $\sqrt f$ |
| Via barrel L, pad C | Goldfarb–Pucel, Johnson formulas; Palace | < 10% vs formula, < 3% vs Palace |
| Via in a 2-port S-parameter | openEMS full wave vs quasi-static π model | Agreement up to $f\approx c/(10\,h\sqrt{\varepsilon_r})$ |
| Thermal slab / fin / spreading | Analytic | < 1% |
| Electro-thermal trace | IPC-2152 charts | Within chart scatter (±20%) |
| JEDEC 2s2p θJA trends | App-note θJA for QFN/SOIC | Trend and rank order; ±25% absolute |
| Layered FVM vs Elmer 3D | Cross-solver | < 5% peak ΔT |
| Dipole far field | Analytic | < 0.5 dB |
| Microstrip radiation | openEMS NF2FF and published measurements (Leone 2003; Hockanson 1996) | ±3 dB DM; trend for CM |
| Cavity resonances | Analytic $f_{mn}$ | < 2% |
| EMC rules | Golden synthetic boards | Exact |

### 5.5 Risks

1. **FastFieldSolvers maintenance and licensing ambiguity.** Activity is low, and the FastHenry2/FastCap2 Windows-port licenses need the actual LICENSE files reviewed. Mitigation: subprocess-only use, pin versions, keep Palace/Elmer backends behind the same interface, and plan the in-house PEEC.
2. **Palace on Windows:** no native build. WSL2/Docker friction for desktop users means Palace can only be a "reference/high-fidelity" path on Windows.
3. **Quasi-static validity.** Users will push Q3D-style models past $\lambda/10$. Every result should carry `validity_fmax` and warn in exported subcircuits.
4. **Passivity and causality of exported models.** Non-passive fits cause SI simulation blow-ups. Make passivity enforcement mandatory.
5. **Thermal boundary conditions dominate the error.** Convection $h$ and component compact models matter more than the solver. Expect ±20–30% absolute without enclosure/airflow data, and present results as relative or with clearly stated assumptions. JEDEC θJA misuse is common, so guard against it in the UI.
6. **EMI estimates are order-of-magnitude.** Closed-form CM estimates are only good to ±10 dB. Label them as screening, and never as compliance prediction.
7. **Rule thresholds are folklore** (20H, 3W, λ/20). Make them configurable, cite the source per rule, and avoid "error" severity for disputed rules.
8. **Return-path inference errors** (zones, splits, multiple references) propagate into EMC, SI, and PI at once. Invest in tests for `ReferenceMap`.

---

## References (selected)

- FastFieldSolvers software and downloads: https://www.fastfieldsolvers.com/software.htm, https://www.fastfieldsolvers.com/dwnld02.htm, https://www.fastfieldsolvers.com/git_repository_content.htm
- FasterCap (LGPL): https://github.com/ediloren/FasterCap; FastHenry2: https://github.com/ediloren/FastHenry2; FastCap2: https://github.com/ediloren/FastCap2
- FastHenry 3.0 mirror: https://github.com/hohe/fasthenry-3.0wr; FreshPorts license: https://freshports.org/science/fasthenry/; FastHenry user guide: https://www.fastfieldsolvers.com/Download/FastHenry_User_Guide.pdf
- AUR FasterCap: https://aur.archlinux.org/packages/fastercap
- FreeCAD EM Workbench: https://wiki.freecad.org/EM_Workbench; Elmer electrostatics in FreeCAD: https://wiki.freecad.org/FEM_EquationElectrostatic, https://wiki.freecad.org/FEM_Example_Capacitance_Two_Balls
- Palace: https://awslabs.github.io/palace/stable/, https://awslabs.github.io/palace/stable/quick/, https://awslabs.github.io/palace/stable/examples/rings/
- Elmer FEM: https://www.csc.fi/web/elmer, https://github.com/ElmerCSC/elmerfem
- bempp-cl: https://github.com/bempp/bempp-cl; JOSS: https://joss.theoj.org/papers/10.21105/joss.02879
- scikit-fem: https://github.com/kinnala/scikit-fem; FEniCSx: https://fenicsproject.org; gmsh: https://gmsh.info
- openEMS NF2FF: https://docs.openems.de/python/openEMS/nf2ff.html
- scikit-rf VectorFitting: https://scikit-rf.readthedocs.io/en/latest/api/vectorFitting.html
- KiCad Thermal Sim: https://github.com/poealex/kicad_thermal_sim; fcad_pcb: https://github.com/realthunder/fcad_pcb
- JEDEC JESD51-7: https://www.jedec.org/node/7480; JEDEC thermal standards overview: https://www.electronics-cooling.com/2019/11/jedec-thermal-standards-developing-a-common-understanding/
- TI SPRA953: https://www.ti.com/lit/an/spra953c/spra953c.pdf; TI SLMA002: https://www.ti.com/lit/an/slma002h/slma002h.pdf; Richtek AN044: https://www.richtek.com/~/media/AN%20PDF/AN044_EN.pdf; ADI: https://www.analog.com/en/resources/technical-articles/thermal-characterization-of-ic-packages.html
- Clemson CVEL EMC expert system: https://cecas.clemson.edu/cvel/emc/expert_systems/PCB/pcb_es_overview.html, https://cecas.clemson.edu/cvel/Projects/cvel_proj_emi-exp.html, https://cecas.clemson.edu/cvel/pdf/EMCS97-444.pdf
- FCC 47 CFR 15.109: https://www.ecfr.gov/current/title-47/chapter-I/subchapter-A/part-15/subpart-B/section-15.109
- Books: H. Ott, *Electromagnetic Compatibility Engineering* (Wiley, 2009); C. Paul, *Introduction to Electromagnetic Compatibility*, 2nd ed. (Wiley, 2006); C. Balanis, *Antenna Theory*, 4th ed.; A. Ruehli et al., *Circuit Oriented Electromagnetic Modeling Using the PEEC Techniques* (2017); F. Incropera, *Fundamentals of Heat and Mass Transfer*; H. Johnson & M. Graham, *High-Speed Digital Design*.
- Papers: Nabors & White, IEEE TCAD 1991 (FastCap); Kamon, Tsuk & White, IEEE TMTT 1994 (FastHenry); Ruehli, IEEE TMTT 1974 (PEEC); Goldfarb & Pucel, IEEE MGWL 1991 (via inductance); Hockanson et al., IEEE TEMC 1996 (CM mechanisms); Gustavsen & Semlyen, IEEE TPWRD 1999 (vector fitting); Kim & Neikirk, IEEE MTT-S 1996 (skin-effect ladder); Lee et al., 1995 (spreading resistance).
