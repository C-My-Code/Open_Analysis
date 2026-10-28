# FEM mesh-convergence study (static Z0, P2 elements)

Generated with `z0_levels` of `packages/oa-impedance/tests/fem/test_convergence.py`: four meshes, `h_max`
and the corner size halved at each level, 20x box, error against the exact solution.

| case | level | P2 dofs | Z0 [ohm] | error vs exact [%] |
|---|---|---|---|---|
| Cohn stripline W/b = 0.5, t = 1e-6 b (exact 100.43245) | 0 | 8 960 | 100.4287 | 0.0038 |
| | 1 | 9 810 | 100.4287 | 0.0037 |
| | 2 | 26 374 | 100.4312 | 0.0012 |
| | 3 | 284 978 | 100.4318 | 0.0007 |
| Richardson (observed order 2.21) | - | - | - | 0.0005 |
| wire over plane h = 2 r (exact 78.96281) | 0 | 2 881 | 78.7772 | 0.2351 |
| | 1 | 12 248 | 78.9648 | 0.0025 |
| | 2 | 63 147 | 78.9656 | 0.0035 |
| | 3 | 375 565 | 78.9656 | 0.0035 |

The wire-over-plane floor (0.0035 %) is the 1024-gon boundary of the circular conductor (the fixture
approximates the circle), not the discretization. The default solver stops at the first level whose
energy change is below `rtol` (1e-3), which on every fixture is level 1 or 2.
