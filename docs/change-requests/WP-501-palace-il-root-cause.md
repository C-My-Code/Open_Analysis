<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->
# WP-501: root cause of the Palace 1.24 dB microstrip insertion loss

Status: partial result from WP-501 (see "Open items" for what is not done).

## Root cause

The 1.24 dB at 10 GHz (25 mm microstrip thru, 0.38 mm on 0.2 mm FR-4) is **not** a discretisation,
dielectric-model or port-normalisation error. It is the **first-order absorbing boundary placed
too close to the line**. The testkit region pads the line by 1 mm above and 2.5 mm to each side.
Palace's first-order `Absorbing` boundary there is an impedance wall that dissipates the
evanescent fringing field of the quasi-TEM mode, which shows up as extra insertion loss that grows
with frequency. About 0.25 dB of the 1.24 dB (20 %) is this artefact.

Evidence (3 sweep points; all runs wave ports, `Solver.Linear` AMS/GMRES, 8 MPI ranks; sheet copper
unless noted). IL = -|S21| in dB:

| Run | Order | DOF (approx.) | IL 1 GHz | IL 5.5 GHz | IL 10 GHz |
|---|---|---|---|---|---|
| WP-301 baseline, coarse, pad 1 / 2.5 mm, ABC1 | 1 | 293 k | 0.146 | 0.717 | 1.238 |
| same, conductors PEC (dielectric only) | 1 | 293 k | 0.086 | 0.564 | 1.030 |
| mesh m2 (47.8 k tets), pad 1 / 2.5 mm, ABC1 | 2 | 331 k | 0.141 | 0.694 | 1.203 |
| mesh m2, box faces PEC instead of ABC | 2 | 331 k | 0.131 | 0.545 | 0.937 |
| mesh m2, second-order ABC | 2 | 331 k | 0.164 | 0.925 | 1.496 |
| big region (pad 5 mm / 6 mm), ABC1 | 2 | 814 k | 0.134 | 0.562 | **0.949** |
| BEM RLGC cascade, 25 mm (this repo, thick Cu, constant eps 4.3) | - | - | 0.115 | 0.492 | 0.845 |

Findings:

1. **Order and mesh are not the cause.** Going from p1 (293 k DOF) to p2 on a 6x coarser mesh moves
   IL(10 GHz) by 0.035 dB (1.238 to 1.203). The thin substrate and edge refinement already resolve
   the line; the mesh is not wavelength limited (changing `gmsh.cells_per_wavelength` 3 to 10
   leaves the mesh unchanged: 228 k tets).
2. **The absorbing boundary is the cause.** Replacing the absorbing faces by PEC lowers IL from 1.203
   to 0.937 dB (-0.27 dB); enlarging the padding to 5 mm / 6 mm gives 0.949 dB, which agrees with the
   PEC-box result. A second-order ABC makes it worse (1.50 dB), so `palace.abc_order = 2` must not be
   used for microstrip.
3. **Remaining gap to BEM (0.949 vs 0.845 dB, 0.10 dB).** Two parts, estimated from the runs above:
   the one-sided finite-thickness surface impedance of Palace 0.18 (`Z = base*(sinh nu + sin nu +
   i(sinh nu - sin nu))/(cosh nu - cos nu)`, source `surfaceconductivityoperator.cpp`) puts about
   0.21 dB of conductor loss on the line against 0.14 dB for the BEM thick-copper section (+0.07 dB),
   and Djordjevic-Sarkar at 5 GHz (eps 4.21, tan d 0.0201) against BEM constant 4.3 / 0.02 (+0.03 dB).
   Both are small and neither was converged further in this session.
4. **Dielectric model.** DS tan d varies 0.7 % over 1 to 10 GHz (eps 3 %), so fixing it at 5 GHz is
   not a cause here; the adapter now offers per-band evaluation (`palace.banded`) for wider sweeps.
5. **Thickness on interior sheets.** The Palace finite-thickness formula tends to `2/(sigma h)` for
   `h << delta` (twice the DC sheet resistance). Copper of 35 um is thick (h/delta > 5) above
   0.1 GHz, where the formula reduces to the one-sided `(1+i)/(sigma delta)`; the current on a
   microstrip lives mostly on the face towards the ground, so a single face term is a good model there.
   For foils thinner than the skin depth it is not.
6. **Wave ports.** Wave-port S11 is below -38 dB at every point (modal reference) and IL does not
   depend on the `Offset`/normalisation at this level; no de-embedding error was found.
7. **Lossless dielectrics hang the mode solve.** `LossTan = 0` makes the wave-port boundary-mode
   solve not return (killed after 4 minutes at 1 GHz, 8 ranks). Use a tiny `LossTan` (1e-6).

Cost: p2 on 331 k DOF, 3 frequencies x 2 excitations: 209 s, 3.4 GB. p2 on 814 k DOF: 597 s, 7.8 GB.
p2 on the original 228 k-tet mesh (about 2 M DOF) exceeded the 12 GB box and was killed. All runs
inside `systemd-run MemoryMax=12G CPUQuota=800%`.

## Changes made in this WP

- `palace.banded`, `palace.eps_tol` (0.2 %), `palace.tand_tol` (2 %): `plan_bands` splits the sweep
  so eps' and tan d change at most that much inside a band; one Palace run per band
  (`config_NN.json`, `postpro_NN`), merged in `collect`.
- Expert keys `occ.edge_divisions`, `occ.thin_elements`, `occ.barrel_segments` for the mesh size.
- `check` warns `PALACE.ABC_NEAR` when an absorbing face is closer than 5 mm to a conductor.

## Open items (not done; to be scheduled)

- Acceptance "Palace within 0.05 dB of R1" cannot be met yet: the remaining 0.10 dB needs a p3 or
  AMR run on a region padded to at least 5 mm, with PEC-vs-sheet copper split, up to 20 GHz. Nothing
  beyond 10 GHz was run. The p3/AMR convergence table and the converged references for B-EM-01 to
  B-EM-06 under `tests/benchmarks/references/em/` were **not produced**.
- Surface-roughness (Huray/Hammerstad) effective sigma per band is not implemented.
- Testkit `microstrip_thru_problem` should pad by at least 5 mm for Palace (owned by WP-201/testkit,
  so only recorded here).
- B-EM-01 target "IL within 0.05 dB of R1" depends on R1 being the BEM number (0.845 dB, constant
  eps 4.3); R1 should use the same Djordjevic-Sarkar model as Palace before the 0.05 dB can be judged.
