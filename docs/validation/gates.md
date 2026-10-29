# ROADMAP M1 gates and the cases that cover them

Hand-maintained map from each ROADMAP M1 validation gate to the case ids (`results.json`) and
case directories. Live numbers are in the generated pages. "Not covered" rows are listed on
purpose.

| ROADMAP gate | Cases | Case directory | Coverage |
|---|---|---|---|
| Closed form: HJ table, Cohn, coupled stripline, CPW | `cf.lock.*` (self-computed), `cf.cohn.*`, `cf.cohn_ec.*`, `cf.cpw.*` | `tests/golden/impedance_closedform/` | covered; HJ rows are self-computed, not independent |
| Closed form vs scikit-rf | none | `packages/oa-impedance/tests/test_vs_skrf.py` | not in this runner |
| IPC legacy (36.8 / 41.1 ohm) | `pub.ipc2141.microstrip`, `pub.wheeler1977.report` | `tests/golden/published/ipc2141_microstrip_legacy`, `.../wheeler_1977_microstrip_report` | IPC independent; Wheeler 41.1 self-computed |
| BEM/FEM analytic (coax, wire, two-wire, zero-t stripline) | `bem.coax_*`, `bem.wire_*`, `bem.two_wire_*`, `bem.cohn.*`, `bem.cohn_ec.*`, `bem.cpw.*`, `fem.*` | `tests/golden/impedance_bem/`, `impedance_fem/` | covered |
| BEM self-checks (L C identity, symmetry) | none | `packages/oa-impedance/tests/test_bem.py` | not in this runner |
| BEM vs FEM, 50 structures | `x.s50.*`, `x.testkit.*` | `tests/golden/crosssolver/bem_vs_fem_50_structures`, `bem_vs_fem_testkit` | covered |
| BEM vs published (SI9000, Wadell) | `pub.pending.*`, `pub.pozar_ex3_6.*` | `tests/golden/published/` | **not covered**: `[verify]` slots only |
| Loss (Bessel, DS, Huray) | none | `tests/golden/physics/` | not in this runner |
| Convergence | none | | not in this runner |
| ERC, one board per rule plus combined | `erc.*` | `tests/boards/kicad10/*/expected.yaml` | covered (11 boards) |
| DC IR analytic | `dc.*` | `tests/golden/pi_dc/`, `physics/via_barrel_r` | covered |
| DC IR cross-check | `x.dc.power_plane_*` (FEM vs raster); `x.dc.kicad_irdrop` | `tests/golden/crosssolver/dc_fem_vs_raster`, `dc_kicad_irdrop_padne` | raster covered; kicad-irdrop/padne pending |
| DC convergence | none | `packages/oa-pi/tests/test_convergence.py` | not in this runner |
| EM sanity: microstrip Z0 and IL vs BEM | `em.openems.*` | `tests/golden/crosssolver/openems_msl_vs_bem_rlgc` | openEMS covered; stripline and Palace pending |
| EM published (notch, patch) | `em.pub.*` | `tests/golden/published/pending_openems_tutorials` | **not covered** |
| EM cross-solver (via, openEMS vs Palace) | `em.palace.*` | `tests/golden/crosssolver/palace_pending_wp301` | **Palace: pending WP-301** |
| Quality gate | `em.openems.quality_pqm` (real output only) | | synthetic fixtures stay in `packages/oa-network` tests |
| Via estimates | none | `tests/golden/physics/` | not in this runner |
| Ingest cross-validation (exit criterion) | `ingest.*` | `tests/golden/ingest_cross/` | covered on the synthetic boards; demo boards need KiCad |
