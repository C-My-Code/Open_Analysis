# WP-506: local quasi-TEM solver instead of oa_impedance for mode-port templates

**Problem.** ADR-0010 section 6 and TASKS WP-506 name the `oa_impedance` 2-D FEM/BEM as the source of
the mode-port templates. The port plane is a rectangular Yee lattice, and the template must be exact
on the Yee edges (the voltage of an in-plane E edge is the potential difference of its two end
nodes). `oa_impedance` solves polygon cross-sections on its own unstructured mesh, so reusing it
needs a projection of its potential onto the lattice (loss of exactness at conductor corners) and a
dependency of `oa_fdtd` on the whole impedance stack for a 60-line capacitance solve.

**Local adapter.** `oa_fdtd.ports.modal.solve_modes` solves the same electrostatic problem with
bilinear (Q1) finite elements on the port-plane lattice (two sparse solves per conductor), and gives
the capacitance matrices with and without the dielectric, the modes, `eps_eff` and the modal
impedance. The input is a per-cell permittivity and per-node conductor labels, so an
`oa_impedance` back end can be dropped in behind the same call.

**Proposed change.** Keep the local solver for the lattice-exact template. Use `oa_impedance` as a
cross-check in WP-507/WP-510 (its Z0 of the cross-section against `ModeSet.z`; they agree to
about 2 % on the testkit microstrip: 47.9 ohm here, 48.8 ohm FEM RLGC at 10 GHz).

**Impact.** None on frozen contracts. `oa_fdtd` now imports `oa_mesh`, `oa_model` and `oa_physics`
(adapter layer, allowed; `oa_fdtd.engine` stays free of them) and `scipy`; the engine import contract
is unchanged and `lint-imports` is clean.
