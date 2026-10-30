<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->
# WP-510 reference data: canonical problems, 2D FEM references, solver comparison

Everything here is regenerated from `refspec.py` (one definition per problem) with
`uv run python tests/benchmarks/references/xs/gen_*.py`. Specs: `specs/canonical-microstrip.toml`.

## Canonical problem (B-EM-01)

0.38 mm x 35 um trace on 0.2 mm Djordjevic-Sarkar dielectric (Dk 4.3, tan d 0.02 at 1 GHz), 35 um
ground plane, sigma = 5.8e7 S/m, roughness off, 25 mm, wave or 50 ohm ports. Note: the testkit
fixture `microstrip_thru_problem` carries a constant 4.3/0.02 material; the openEMS and Palace
runs here replace it by the DS model so that all solvers see the same dielectric.

## R1 (`R1/reference.json`): 2D FEM RLGC cascade

P2 FEM, 4 uniformly refined meshes, impedance-boundary eddy solve, Richardson extrapolation per
frequency (0.1-20 GHz). IL at 10 GHz over 25 mm:

| meshes | IL [dB] | Z0 [ohm] | eps_eff | alpha_c [dB] | alpha_d [dB] |
|---|---|---|---|---|---|
| 1 | 0.83322 | 49.172 | 3.09127 | 0.14005 | 0.69313 |
| 2 | 0.83127 | 49.195 | 3.08982 | 0.13855 | 0.69269 |
| 3 | 0.82914 | 49.216 | 3.08855 | 0.13680 | 0.69231 |
| 4 | 0.82829 | 49.224 | 3.08775 | 0.13619 | 0.69207 |
| extrapolated | **0.82771 +- 0.0006** | 49.231 | 3.0864 | | |

Wall 1684 s for all four levels plus a variant. Quasi-static: no dispersion, no radiation.

## Agreement at 10 GHz, 25 mm line (IL, dB)

| solver (configuration) | IL | minus R1 | tolerance | note |
|---|---|---|---|---|
| R1 | 0.8277 | 0 | | |
| BEM (default mesh, DS) | 0.8315 | +0.004 (max over 0.1-20 GHz) | 0.05 | alpha_c +4 %, alpha_d -0.3 % |
| openEMS skin, medium mesh, lumped | 0.857 | +0.029 | 0.05 | within 0.05 dB to 13 GHz; +0.13 at 20 GHz |
| openEMS skin, fine mesh | 0.842 | +0.015 | 0.05 | |
| openEMS PEC (no conductor loss), medium / fine | 0.761 / 0.747 | | | Pozar alpha_d 0.706, R1 0.692: mesh error +0.07 / +0.055 |
| Palace p2 sheet copper, lambda/3 mesh, eps at 10 GHz | 1.018 | +0.19 | 0.05 | does NOT meet the gate |
| Palace p2 PEC | 0.807 | | | dielectric-only: +0.115 over R1 alpha_d |

Palace runs: 5 mm line (63k tets, 3.5 GB, 276-357 s for two frequencies, 4 ranks, 12 GB box),
scaled to 25 mm (matched uniform line, S11 < -39 dB); `R1/palace_runs.json`. Residual
decomposition for Palace (10 GHz, 25 mm equivalent):

- discretisation of the dielectric loss on the coarse p2 mesh: +0.115 dB (PEC run vs R1 alpha_d);
  openEMS shows the same sign (+0.07 medium, +0.055 fine mesh);
- interior-sheet copper: Palace adds 0.211 dB (sheet minus PEC) against R1 alpha_c 0.136; the
  BEM current distribution gives a top+bottom summed-current sheet loss 1.41x the two-face loss,
  i.e. 0.192 dB (+0.056 dB), the remaining +0.02 dB is mesh;
- dielectric evaluated once instead of per frequency: R1 variant `ds_frozen_5ghz` is +0.013 dB at
  10 GHz (the earlier 0.03 estimate is not confirmed); Palace here used eps at 10 GHz, so zero.

A p3 run on the same mesh was lost to a machine crash and is not done. The Palace acceptance
(0.05 dB of R1) therefore remains OPEN: it needs a finer mesh than fits the 12 GB box, or p3.

openEMS `skin` copper contributes 0.096 dB (skin minus PEC) against R1 0.136: its sheet model
under-predicts the edge-crowded loss by about 30 %, which partly cancels the dielectric
mesh error above. Z0 of the openEMS wave-port microstrip was not re-measured (gap).

## B-IMP-02 (`B-IMP-02/<name>/reference.json`, 24 cases)

12 geometries x {35, 18 um}, trapezoid etch (top narrower by t), lossless static eps_r, copper PEC.
Reference = Richardson over 4 P2 meshes; `error_estimate_pct` is |extrapolated - finest|
(max 0.16 %: the 0.05 % target is met for 13 of 24 cases only; stripline/pair cases converge
fast, narrow microstrips slowly). BEM errors against the reference, before and after WP-510
(default mesh, percent):

| | Z0/Zdiff max | eps_eff max |
|---|---|---|
| before | 0.335 | 0.532 |
| after | 0.144 | 0.167 |

CPW on a half space (B-IMP-01, exact conformal map): +1.03 % -> -0.045 % (eps_r 1) and
+1.03 % -> -0.041 % (eps_r 9.8), inside 0.05 %. The "4x BEM within 0.1 %" check is stored per
case (`bem_4x.vs_default_pct`).

## Not done / [verify]

- B-IMP-03 converged eddy-current references beyond R1 (R1 covers microstrip alpha_c, alpha_d
  0.1-20 GHz), the DC references (B-DC-01 `dc/`), Huray K(f) curve, 50 GHz.
- Published slots (Polar SI9000, Wadell, Bryant-Weiss, kicad-irdrop 3.04 mOhm, padne 2.97 mOhm,
  Pozar 3.6, openEMS tutorials) stay **[verify]**: no source could be checked here (a web search
  for the kicad-irdrop/padne figures found none).
