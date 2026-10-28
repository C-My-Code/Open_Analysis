# Accuracy classes and tolerance tiers

Every result item states how far to trust it (`ResultItem.accuracy`). Quote the class and tolerance next to every number.

| `accuracy_class` | Meaning |
|---|---|
| `rule` | Geometric rule check: exact geometry, heuristic threshold. |
| `closed_form` | Analytic or empirical formula (Hammerstad-Jensen, Cohn, Wheeler, IPC-2141). |
| `2d_field` | 2D quasi-TEM field solve (BEM, FEM) of a cross-section. |
| `2d_sheet` | 2D conduction on copper sheets plus via resistors (DC IR). |
| `lumped` | Lumped or ladder circuit models. |
| `3d_quasi_static` | FasterCap, FastHenry or Palace electrostatic/magnetostatic (M2). |
| `3d_full_wave` | openEMS or Palace driven (full wave). |
| `surrogate` | ML model trained on this project's solver outputs (M3). |
| `estimate` | Order-of-magnitude screening (EMI DM/CM estimates). |
| `unknown` | The producer did not say. Treat as unverified. |

`validity` is one of `in_range`, `extrapolated`, `invalid`, `unknown`. An `invalid` result never makes a rule fail: it produces a `RESULT.INVALID_INPUT_RANGE` warning. `f_min`/`f_max` give the valid band, `converged` the mesh/energy convergence, and `quality` the S-parameter gate verdict.

## Tolerance tiers

- **T0 analytic**: at most 0.5 % (impedance), 0.1 % (DC resistance), 1 % (resonance frequencies).
- **T1 published**: 1-3 % against literature or vendor tables.
- **T2 cross-solver**: 2-5 % between independent solvers.
- **T3 measured**: within the stated measurement uncertainty.
