# DC IR drop

[Back to the summary](index.md). Generated 2026-10-29T13:13:28+00:00 from commit `0cc834dc47ea` (worktree-agent-a556a5506fa1604ff) on Windows 11, Python 3.13.14; full run 456 s.

![Error as a fraction of the tolerance](plots/dc-ir.svg)

## dc-ir

| Case | Source | Class | Indep. | Quantity | Expected | Got | Error | Tolerance | Status |
|---|---|---|---|---|---|---|---|---|---|
| `dc.strip_uniform`<br>`tests/golden/pi_dc/strip_uniform`<br>Uniform strip, R = Rs L / W | analytic: R = Rs L / W, Rs = 1/(sigma t), sigma = 5.8e7 S/m, t = 35 um (ROADMAP gate 0.1 %) | analytic | yes | R | 0.0049261 ohm | 0.0049261 ohm | +0.0000 % | 0.1 % | pass |
| `dc.annulus`<br>`tests/golden/pi_dc/annulus`<br>Annulus between ring electrodes, R = Rs ln(b/a) / 2 pi | analytic: R = Rs ln(b/a) / (2 pi) (ROADMAP gate 0.1 %) | analytic | yes | R | 1.8053e-04 ohm | 1.8043e-04 ohm | -0.0509 % | 0.1 % | pass |
| `dc.via_barrel_roadmap`<br>`tests/golden/physics/via_barrel_r`<br>Via barrel R (oa_physics), ROADMAP figure 1.27 mohm | analytic: R = rho h / (pi (ro^2 - ri^2)), drill 0.30 mm, plating 25 um, h 1.6 mm, rho = 1/5.8e7 | analytic | yes | R | 0.00127 ohm | 0.0012772 ohm | +0.5694 % | 1 % | pass |
| `dc.via_array_n9`<br>`tests/golden/pi_dc/two_layer_vias_n9`<br>Nine parallel via barrels, R = R_barrel / 9 | analytic: R = R_barrel / N with R_barrel = rho L / (pi (ro^2 - ri^2)); pads span the array, so no sheet spreading | analytic | yes | R | 1.4191e-04 ohm | 1.4191e-04 ohm | -0.0000 % | 0.5 % | pass |
| `dc.board.power_strip`<br>`tests/boards/kicad10/power_strip`<br>WP-102 board 'power_strip' vs expected.yaml: strip_end_to_end | tests/boards/kicad10/power_strip/expected.yaml dc.cases (R = Rs * L / W, Rs = 1 / (sigma * t), sigma = 5.8e7 S/m, t = 35 um, L = 50 mm, W = 5 mm) | analytic | yes | R | 0.0049261 ohm | 0.004927 ohm | +0.0186 % | 0.5 % | pass |
| `dc.board.power_two_layer_vias`<br>`tests/boards/kicad10/power_two_layer_vias`<br>WP-102 board 'power_two_layer_vias' vs expected.yaml: via_array_pad_to_pad | tests/boards/kicad10/power_two_layer_vias/expected.yaml dc.cases (R_via = L / (sigma * A), A = pi/4 (d^2 - (d - 2 tp)^2), d = drill 0.3 mm (KiCad drill is th... | analytic | yes | R | 1.4191e-04 ohm | 1.4191e-04 ohm | -0.0000 % | 0.5 % | pass |

## dc-ir/cross-solver

| Case | Source | Class | Indep. | Quantity | Expected | Got | Error | Tolerance | Status |
|---|---|---|---|---|---|---|---|---|---|
| `x.dc.power_plane_testkit.plain`<br>`tests/golden/crosssolver/dc_fem_vs_raster`<br>FEM vs raster FVM, testkit power_plane board | cross-solver (in-repo): oa_pi FEM vs the raster finite-volume reference oa_pi.reference.solve_raster at h/4, on the oa_testkit power_plane board (with and wi... | cross-solver | no | voltage drop | 0.024062 V | 0.023899 V | -0.6792 % | 1 % | pass |
| `x.dc.power_plane_testkit.neck`<br>`tests/golden/crosssolver/dc_fem_vs_raster`<br>FEM vs raster FVM, testkit power_plane board with 6 mm neck | cross-solver (in-repo): oa_pi FEM vs the raster finite-volume reference oa_pi.reference.solve_raster at h/4, on the oa_testkit power_plane board (with and wi... | cross-solver | no | voltage drop | 0.029892 V | 0.029688 V | -0.6828 % | 1 % | pass |
| `x.dc.power_plane_wp102`<br>`tests/golden/crosssolver/dc_fem_vs_raster`<br>FEM vs raster FVM, WP-102 power_plane | cross-solver (in-repo): oa_pi FEM vs the raster finite-volume reference oa_pi.reference.solve_raster at h/4, on the oa_testkit power_plane board (with and wi... | cross-solver | no | voltage drop | 0.014353 V | 0.014241 V | -0.7823 % | 1 % | pass |
| `x.dc.kicad_irdrop`<br>`tests/golden/crosssolver/dc_kicad_irdrop_padne`<br>[verify] kicad-irdrop 4-layer board (3.04 mohm) | [verify] kicad-irdrop 4-layer board: published 3.04 mohm, padne 2.97 mohm (ROADMAP DC IR cross-check, gate <= 3 %). Not run: the board and the external tools... | cross-solver | yes | R | 0.00304 ohm | - | - | 3 % | pending |
