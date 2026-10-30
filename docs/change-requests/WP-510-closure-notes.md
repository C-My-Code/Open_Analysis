# WP-510: closure notes and requested doc edits

Problem: WP-510 owns no docs paths, so these edits to `docs/FOLLOWUPS.md`, `docs/BENCHMARKS.md` and
`tests/testkit` are requested from the integrator. Data and tables: `tests/benchmarks/references/xs/README.md`.

## FOLLOWUPS items closed or changed

- Closed: WP-202 "BEM eps_eff 0.57-0.59 % low on narrow high-er microstrips". Cause: in
  `bem/geometry.py` the panel positions were found with `np.interp` on the cumulative
  integral of dl/size between samples; across a sample interval where the target size varies
  by a factor of 60 this made the panels next to every strip corner uniform (4.5 um instead of
  0.3 um), so the interface charge singularity was unresolved. Fixed by inverting the integral
  exactly per interval (size is linear there); defaults `s_min_rel` 1e-3 -> 3e-4 and
  `min_feature_ratio` 0.25 -> 0.05. BEM vs converged FEM over the 24 B-IMP-02 structures: eps_eff
  0.532 % -> 0.167 % max, Z 0.335 % -> 0.144 %; runtime single 24 ms, pair 48 ms (20 freqs).
- Closed: WP-105 "free-space CPW 0.22 % off". The exact conformal-map cases are now -0.045 % and
  -0.041 % (was +1.03 % with the old panelling on the same geometry); gate 0.05 %. Residual
  -0.05 % does not shrink with panel density, truncation or depth: a floor of the constant-charge
  ribbon edge treatment, not investigated further.
- Closed: WP-205 "openEMS Z0/loss with sheet copper": `openems.conductor_model = "skin"` (default).
  Changed: the "single-pole Debye 11 % tan d" item is closed by the multi-pole fit (0.04 %).
- Open (new): Palace p2 on the lambda/3 mesh is +0.19 dB over R1 at 10 GHz; see the README.
- Open: openEMS Z0 re-measurement on wave ports, stripline stall, MSL notch, B-IMP-03 / B-DC-01
  converged references, all published slots (kept [verify]).
- Process note for BENCHMARKS B-EM-01: "openEMS 0.74 dB (sheet copper)" was conductor-lossless
  (PEC gives 0.761 dB); with real copper it is 0.857 dB.

## Requested contract/doc changes

1. `oa_testkit.emproblems.microstrip_thru_problem` uses a constant 4.3/0.02 material while
   BENCHMARKS B-EM-01 specifies Djordjevic-Sarkar: make the fixture DS (the runners here override
   it).
2. BENCHMARKS B-IMP-02: the "error estimate <= 0.05 %" is met for 13 of 24 references
   (max 0.16 %) with 4 P2 levels; either relax to 0.2 % or add levels / adaptive refinement.
3. BENCHMARKS B-EM-01: R1 is quasi-static; above about 13 GHz the 3D solvers legitimately differ
   from it (openEMS +0.13 dB at 20 GHz), so the "every frequency to 20 GHz" gate needs a 3D
   reference (R2) above 13 GHz.
