# oa-em

The `em.sparams` analysis (WP-205): board region -> `EMProblem` -> solver adapter -> S-parameters.
Solver neutral: it talks to `oa_framework.em.SolverAdapter` (entry point group
`open_analysis.em_adapters`, with a built-in fallback for `openems`).

## Public API

```python
from oa_em import EmSparams, EmSparamsConfig, build_problem
```

- `EmSparams` (`kind="em.sparams"`, registered through `open_analysis.analyses`).
- `EmSparamsConfig`: `solver`, `region` (nets, reference_nets, bbox or automatic margin, layers),
  `frequency`, `mesh`, `convergence`, `boundary`, `ports`, `fix_quality`, `refinement_check`,
  `energy_margin_db`, `z0_tolerance`, `estimate_scale`.
- `build_problem(geometry, config) -> Built`: the pure, deterministic region -> `EMProblem` step
  (cut region, ports, boundaries, mixed-mode pairs). Raises `SetupError` with diagnostics.

## Flow

1. `validate_setup`: builds the problem, checks adapter capabilities, calls `adapter.check`,
   compares configured port impedances with the closed-form line impedance (`impedance` service,
   warning above 10 %).
2. `plan`: `prepare` task (check + mesh statistics) and one `run:<variant>:<k>` task per adapter
   command (one per excitation for FDTD). Run tasks carry `Resources(est_seconds, memory_gb,
   needs=(adapter.tool,))`, so `LocalJobService` enforces the budget before anything starts. With
   `refinement_check` the same tasks run again on a finer mesh.
3. `run_task`: rebuilds the problem (deterministic), prepares the run directory, runs the command
   through `ctx.run_external` and stores the output files as artifacts.
4. `reduce`: restores the run directory, `adapter.collect`, energy and mesh-refinement
   convergence, port impedance check, **`oa_network.gate`** (optionally with `fix_quality`),
   Touchstone, mixed-mode conversion for differential port pairs, a JSON sidecar (region, port map,
   stackup hash, mesh stats, convergence, quality, runtime) and a mesh preview artifact.
   Status: `failed_quality` (gate verdict fail), `warning` (gate warn, not converged, EM.*
   warnings), `error` (no S-parameters), else `ok`.

## Ports and boundaries

- `trace_end` -> `EMWavePort` on the face the trace crosses. With an automatic region the trace
  end is extended to the face (`EM.PORT_EXTENDED`). Differential (2 nets) -> two single-ended
  wave ports `<name>_P`, `<name>_N` plus mixed-mode conversion (`D_*`, `C_*` ports).
- `lumped` -> `EMLumpedPort` through `resolve_terminal`; `coax` -> `EMCoaxPort`.
- Wave faces absorb. Reference planes at the z faces give PEC, otherwise absorbing with air
  padding (`max(lambda0/10, 5 x stack height)`, at most `lambda0/4`). Side walls are PEC when every
  signal conductor lies between reference planes (stripline), else absorbing.

## Tests and golden data

`tests/golden/em_flow/` (EMProblem snapshot). Unit tests use a mock adapter through
`oa_testkit.harness`. `external("openems")` tests (`test_external_openems.py`) run the real thing
through `LocalJobService`: microstrip thru vs the analytic line and the via transition.
`docs/change-requests/WP-205-*.md` lists the contract issues.

## Theory and validation

Equations, numerical method, validity range, error budget and validation evidence: [THEORY.md](THEORY.md).
