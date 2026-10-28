# oa-pi

Power integrity. WP-204 (DC IR drop, analysis `pi.dc`) is implemented; AC PDN is WP-403.

Layer L4 (interfaces.md section 0). Contracts: interfaces.md sections 3.3, 3.4 (`pdn` service),
4.1, 6, 11 and ai-integration.md section 4 and 13 (accuracy, summaries, structured errors).

## Public API

```python
from oa_pi import PiDcAnalysis, DcConfig, build_network, solve_fem, PdnService
from oa_pi.reference import solve_raster       # independent raster FVM cross-check

net = build_network(board, geometry, cfg)      # solver-neutral network (sheets, barrels, pins)
sol = solve_fem(net)                           # DcSolution: fields, pins, barrels, scalars
ref = solve_raster(net, h=0.2e-3)              # same problem, different discretization
```

Entry points: analysis `pi.dc` (`open_analysis.analyses`), service `pdn`
(`open_analysis.services`): `PdnService().bind(board).dc_solve(rail, T_field)` re-solves with
`rho(T)` (uniform K or `f(layer, xy) -> K`) for the M2 electro-thermal loop.

## Config (`DcConfig`, the interfaces.md section 6 example)

`rail {net, return}`, `vrm {component, pins, return_pins, vset, r_out, sense {pos, neg}}`,
`loads [{component, nets, pins, return_pins, current, model: pin_group|equal_split, name}]`,
`temperature`, `mesh {h_plane, h_trace_factor, via_ring_segments, solver}`,
`limits {drop_fraction, via_current_density}`. Without `rail.return` the return is an ideal 0 V.

## Model

- One 2D sheet per (layer, net) from `BoardGeometry.copper` (zone fills are actual copper, so
  thermal-relief spokes and antipads are meshed as geometry). `R_s = rho(T)/t` from `oa_physics`.
- P1 FEM, vectorized COO assembly, on `oa_mesh.mesh_copper_sheets` meshes.
- Via/plated-pad barrels: one resistor `rho L / (pi (ro^2 - ri^2))` between every pair of copper
  layers they touch, tied to **all nodes of a ring of radius ro** (drill radius) on each layer.
  A barrel whose centre lies in a pin pad ends on that pin. Length convention: the barrel spans
  from the top face of its first retained layer to the bottom face of its last.
- Pins: ideal equipotential pad (the pad outline is a mesh boundary tied to the pin); pins of a
  group are merged exactly. Loads are `pin_group` (default) or `equal_split`.
- Regulator: Dirichlet, Thevenin (Norton, still SPD), or remote sense (MNA extra unknown,
  SuperLU). Ground return solved together.
- Refusals (structured `code/path/hint`): unfilled zones (`PI_ZONE_UNFILLED`), unknown nets,
  components, pins (`SETUP_INVALID`), a load with no copper path to the regulator (`PI_NO_PATH`).
  Stale fills and floating copper groups are warnings. Dead-end copper (fewer than two
  attachments) is dropped.
- Solvers: SuperLU; `pypardiso` or `scikit-sparse` (CHOLMOD) when installed; AMG-PCG (`pyamg`)
  above 1e6 unknowns.
- Results: `field` (V, dV, |J| per layer), `table` x3 (loads with PASS/FAIL, vias, pins),
  `scalars` (effective R, losses, worst drop, worst via current/density, worst neck, KCL
  residual). All items carry `accuracy_class="validated"` plus a `accuracy.2d_sheet` validity flag
  (see `docs/change-requests/WP-204-accuracy-class.md`); the bundle summary states the worst load
  drop and worst via current without inlining arrays.

## Convergence (documented)

- Ring contacts (all contacts in the solver) are mesh independent: < 0.5 % per 2x refinement,
  power_plane drop moves 0.15 % over 8x refinement.
- A point contact (radius 0) is not used: its resistance grows by `Rs ln 2 / (2 pi)` per halving
  of h (log divergence, no convergence order); the test `test_convergence.py` shows it.

## Golden suite and validation

`tests/golden/pi_dc/` (strip, annulus, N=9 via array, the two WP-102 `expected.yaml: dc`
cases). Tests: analytic (strip 0.1 %, annulus 0.1 %, series/parallel vias exact, WP-102 boards
0.5 %), conservation/KCL (< 1e-9), mesh convergence, FEM vs raster (`power_plane_board`
h/4: < 1 %), the analysis through `oa_testkit.harness`, 3e5-unknown timing (`slow`).

Not done: the nightly `external` kicad-irdrop oracle (needs a network download), KiPI/padne
comparison, multi-rail trees and series elements.
