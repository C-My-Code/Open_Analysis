# oa-pi: theory, error budget and validation

DC IR drop on power rails (analysis `pi.dc`). Code: `packages/oa-pi/src/oa_pi/dc/` (`network.py`, `fem.py`, `terminals.py`, `solvers.py`) and the independent cross-check `reference/raster.py`. AC PDN impedance is a later work package (WP-403) and has no theory page yet. See the [theory index](../../docs/theory/README.md).

## 1. Problem statement

**Input.** A board with copper geometry (`BoardGeometry.copper`, zone fills included), a rail definition (net, optional return net), a regulator (component, pins, set voltage [V], output resistance [ohm], optional remote sense), loads (pin groups with current [A], `pin_group` or `equal_split`), a temperature [K or C as configured], and mesh controls.

**Output.** DC node voltages [V], current density [A/m] per layer and net, per-pin currents [A], per-via currents and current densities, effective resistance [ohm], worst load drop [V] and its fraction of the set voltage, losses [W], worst neck, a KCL residual, and PASS/FAIL tables against `limits` (`drop_fraction`, `via_current_density`).

The model is a resistive network: 2D sheets of copper joined by via-barrel resistors. It computes the steady-state DC solution only.

## 2. Governing equations and formulation

### 2.1 Sheet conduction

Each (layer, net) copper region is a thin sheet with sheet resistance $R_s=\rho(T)/t$ [ohm/sq], $\rho(T)=\rho_0[1+a(T-T_\text{ref})]$ from `oa_physics.resistivity` ($\rho_0=1/\sigma$ from the material; the tests use $\sigma=5.8\times10^7$ S/m). In a source-free region

$$\nabla\cdot\Big(\frac1{R_s}\nabla V\Big)=0,\qquad \mathbf K=-\frac{1}{R_s}\nabla V\ [\text{A/m}] .$$

### 2.2 P1 finite elements (`dc/fem.py`)

Sheets are meshed with `oa_mesh.mesh_copper_sheets` (triangles; via rings and pad outlines are mesh features). With linear shape functions on a triangle of area $A_e$ and coefficient vectors $\mathbf b,\mathbf c$ (the gradients of the shape functions times $2A_e$), the element matrix is

$$K_e=\frac{\mathbf b\mathbf b^T+\mathbf c\mathbf c^T}{4A_eR_s},$$

assembled as one vectorised COO list (per-element $R_s$ evaluates $\rho$ at the element centroid, so a temperature field `f(layer, xy)` is supported). Zone fills are actual copper polygons, so thermal-relief spokes and antipads are geometry, not approximations.

### 2.3 Via barrels and contact modelling

A via or plated pad barrel is a resistor between every pair of copper layers it touches:

$$R_\text{barrel}=\frac{\rho L}{\pi(r_o^2-r_i^2)},$$

with $r_o$ the drill radius, $r_i=r_o-t_\text{plating}$, $L$ from the top face of the first retained layer to the bottom face of the last (the length convention is documented in the README). On each layer the barrel is tied to **all nodes of a ring of radius $r_o$** (an n-gon with at least 16 vertices). All ring nodes are merged into one unknown, which makes the ring an exact equipotential.

The reason for a ring rather than a point: a point contact on a 2D sheet has a logarithmically divergent resistance, $\Delta R=R_s\ln2/(2\pi)$ more per halving of the mesh size, so it has no mesh-converged value. A ring of finite radius has the spreading resistance $\approx\frac{R_s}{2\pi}\ln(r_\text{far}/r_o)$, which converges. `test_convergence.py` shows both behaviours. The ring is a modelling choice: it assumes the current enters the sheet uniformly around the drill circle (an ideal equipotential pad/annular ring), which is accurate when the pad annulus is wide compared with the plating and conducts better than the sheet it feeds.

Pins (component pads) are ideal equipotentials: the pad outline is a mesh boundary and its nodes are merged. Pins of a group are merged exactly. A barrel whose centre lies in a pin pad ends on that pin. Dead-end copper (fewer than two attachments) is dropped. Floating copper groups give a warning.

### 2.4 Terminals (`dc/terminals.py`)

The regulator is a Dirichlet condition (ideal), a Thevenin source (Norton form, still symmetric positive definite), or a remote-sense condition (modified nodal analysis with an extra unknown, solved by SuperLU). Loads are current sinks at pin groups. The return is solved together with the supply when `rail.return` is given, otherwise it is an ideal 0 V.

### 2.5 Linear solvers (`dc/solvers.py`)

The assembled system is symmetric positive definite. Solvers: SuperLU (default); `pypardiso` or `scikit-sparse` (CHOLMOD) if installed; AMG-preconditioned CG (`pyamg`) above $10^6$ unknowns (`AMG_THRESHOLD`). `kcl_residual` reports the relative Kirchhoff current-law residual of the solution. GPU use is through `oa_accel` (see the [oa-accel THEORY](../oa-accel/THEORY.md)); the DC result is held to a node-voltage tolerance of $10^{-9}V_\text{set}$ against the CPU.

### 2.6 Independent reference (`reference/raster.py`)

The same `DcNetwork` is discretised differently: a square raster of cell size $h$, neighbouring copper cells joined by conductance $1/R_s$ (independent of $h$ for square cells), copper where the cell centre lies inside the polygons (staircase edges). Barrel contacts use the smallest lattice disc whose equivalent radius (0.1986 h for one cell, from a local lattice solve) is at least the barrel radius, plus the excess spreading resistance $R_s\ln(r_\text{eq}/r_o)/(2\pi)$ in series. Terminals reuse `oa_pi.dc.terminals`, so the regulator, load and pin-merging algebra is shared and **not** independently checked by this cross-check. It is a check on the sheet discretisation and the contact treatment, not on the circuit algebra.

## 3. Assumptions and validity range

- **DC only.** No skin effect, no inductance, no AC. Valid for DC and for slowly varying loads where the sheet's current distribution has settled; for a switching load use the planned AC PDN analysis.
- **Thin sheet.** Current is uniform through the copper thickness. Valid when lateral feature sizes are large compared with $t$ (35 µm foil with features of a few hundred µm or more). Strongly 3D current crowding at via-to-trace junctions on thick copper is not modelled.
- **Ideal pads.** Pins are equipotential; the resistance of a real pad-to-ball or solder joint, and the current distribution inside a pad, are not represented. Pads are drawn as holes in field plots.
- **Ring contacts** for vias (see 2.3). A via-in-pad or an array of vias closer than a few drill diameters shares current; the ring model accounts for neighbours only through the sheet.
- **Barrel resistance** assumes uniform plating thickness and a conductivity equal to that of the bulk material (plated copper is typically somewhat more resistive than rolled foil; whether the model exposes a separate plating conductivity was not checked [verify]).
- **Temperature**: $\rho(T)$ is linear; $T$ is an input (uniform or a field), not computed unless the M2 electro-thermal loop is used.
- **Scope**: no multi-rail trees or series elements (fuses, ferrites, sense resistors) yet.
- **Refusals**: unfilled zones (`PI_ZONE_UNFILLED`), unknown nets, components or pins (`SETUP_INVALID`), no copper path to the regulator (`PI_NO_PATH`). Stale fills are a warning.

## 4. Error sources and magnitudes

| Source | Magnitude | Evidence |
|---|---|---|
| FEM against analytic strip $R=R_sL/W$ | 0.0000 % (tolerance 0.1 %) | `dc.strip_uniform` |
| FEM against analytic annulus $R_s\ln(b/a)/2\pi$ | -0.051 % (tolerance 0.1 %) | `dc.annulus` |
| WP-102 `power_strip` board against expected.yaml | 0.0186 % (tolerance 0.5 %) | `dc.board.power_strip` |
| Nine parallel via barrels, $R_\text{barrel}/9$ | 0.0000 % | `dc.via_array_n9`, `dc.board.power_two_layer_vias` |
| Via barrel R against the 1.27 mohm ROADMAP figure | +0.57 %; model value 1.2772 mohm, the ROADMAP number is rounded | `dc.via_barrel_roadmap` |
| FEM against raster FVM (h/4), 3 power-plane boards | worst -0.68 % (tolerance 1 %) | `x.dc.power_plane_*` |
| Mesh convergence of ring contacts | < 0.5 % per 2x refinement; power_plane worst drop moves 0.15 % over 8x refinement | `tests/test_convergence.py`, README |
| KCL residual | < $10^{-9}$ | tests |
| Pad and ring-contact idealisation, thin-sheet assumption | **not quantified** against a 3D solver or measurement | no case |
| Copper thickness, plating thickness, $\sigma$, temperature (input error) | frequently several percent; the largest term in a real design | planned UQ, [ADR-0012](../../docs/adr/0012-uncertainty-quantification.md) |

## 5. Validation evidence

Source: `docs/validation/results.json` (six `dc-ir` cases, all with analytic references, all pass; three cross-solver cases pass; one pending).

| Case | Reference | Tolerance | Result |
|---|---|---|---|
| `dc.strip_uniform` | analytic | 0.1 % | 0.0000 % |
| `dc.annulus` | analytic | 0.1 % | -0.0509 % |
| `dc.board.power_strip` | analytic via `expected.yaml` (constructed by `tools/boardgen`) | 0.5 % | 0.0186 % |
| `dc.via_array_n9`, `dc.board.power_two_layer_vias` | analytic | 0.5 % | 0.0000 % |
| `dc.via_barrel_roadmap` | analytic (oa_physics) | 1 % | +0.5694 % |
| `x.dc.power_plane_testkit` (plain, 6 mm neck), `x.dc.power_plane_wp102` | in-repo raster FVM (cross-solver) | 1 % | worst -0.679 % |
| `x.dc.kicad_irdrop` | published kicad-irdrop 3.04 mohm and padne 2.97 mohm | 3 % | **pending**; values [verify] |

These test the sheet physics, the contact model and the assembly on cases with exact answers, and the FEM against a second discretisation. They do not test real boards against measurement or a commercial tool.

**Known gaps and open issues (FOLLOWUPS WP-204).**

- The kicad-irdrop / padne / KiPI cross-checks were not run; B-DC-01's "converged" and "published" parts are `ref-pending` / `[verify]` (`docs/validation/benchmarks/dc-ir.md`).
- Multi-rail trees and series elements are not done.
- Pads are ideal equipotentials.
- There is no `2d_sheet` literal in `oa_results.AccuracyClass`: results use `accuracy_class="validated"` plus an `accuracy.2d_sheet` validity flag (change request WP-204-accuracy-class). `docs/specs/ai-integration.md` lists `2d_sheet` as the intended class.
- Scale: the 3e5-unknown case takes 10.5 s total (mesh 8.8 s, solve 1.4 s) against a 5 s target (B-DC-02, `ready`, not yet gated); 1e6 and raster-1e7 cases are not run.
- Two earlier problems were fixed in WP-211: slow meshing, and wrong hole-mode contact ring nodes (embedded mode is what `oa-pi` uses).

## 6. References

- Method: standard linear finite elements for the Laplace equation; e.g. O. C. Zienkiewicz, R. L. Taylor, *The Finite Element Method*, 6th ed., Butterworth-Heinemann, 2005 [verify edition].
- Spreading resistance of a circular contact on a thin sheet: R. Holm, *Electric Contacts*, 4th ed., Springer, 1967 [verify; the code derives it from the lattice solve, not from Holm].
- Project: `docs/research/04-power-integrity.md` section 1.1; `docs/validation/dc-ir.md`; `docs/BENCHMARKS.md` B-DC-01 and B-DC-02; change requests `docs/change-requests/WP-204-*.md`.
- Published DC IR comparison values (kicad-irdrop 3.04 mohm, padne 2.97 mohm): as cited in ROADMAP M1 [verify against the repositories before relying on them].
