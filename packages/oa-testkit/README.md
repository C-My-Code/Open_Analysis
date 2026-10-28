<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# oa-testkit

Shared test infrastructure (import name `oa_testkit`, layer L1 in interfaces.md section 0).
Used by every package's tests; never imported by production code.

## Public API

| Module | Contents |
|---|---|
| `oa_testkit.builders` | `microstrip_board`, `stripline_board`, `diff_pair_board`, `cpwg_board`, `split_plane_board`, `via_transition_board`, `power_plane_board`; helpers `make_stackup`, `rect_ring`, `circle_ring`. All keyword-parameterized in SI units, deterministic, `validate_board(strict=True)` clean. |
| `oa_testkit.xsections` | `all_cases()`, `get_case(name)` returning `XsCase(name, xs, expected, rtol, source, kind, note)`. |
| `oa_testkit.emproblems` | `microstrip_thru_problem` (2 lumped ports), `stripline_thru_problem` and `via_transition_problem` (2 wave ports each). |
| `oa_testkit.analytic` | Dependency-free reference formulas: coax, wire over plane, two-wire, Cohn stripline and edge-coupled stripline, CPW, Hammerstad-Jensen, Wheeler stripline; `ellipk` by AGM. |
| `oa_testkit.golden` | `assert_close_to_golden(actual, suite, case, ...)`, `load_case`, `write_case`. |
| `oa_testkit.harness` | `run_inline(analysis, board, setup, registry, ...)` and `run_inline_full(...)`. |
| `oa_testkit.plugin` | pytest plugin (entry point `pytest11`): `--update-golden`, markers, `update_golden` fixture. |

### Cross-section expected values

`XsCase.kind` says where the numbers come from:

- `analytic`: exact formulas (coax, wire over plane, two-wire line, Cohn zero-thickness stripline
  and edge-coupled stripline, zero-thickness CPW). Tolerances 1e-4 (round conductors) to 5e-3
  (thin strips standing in for zero thickness).
- `formula_lock`: Hammerstad-Jensen microstrip values from our own transcription of the 1980
  formulas. Marked "self-computed, needs independent check" in `source`. They lock a closed-form
  implementation to 1e-6 and give a 1.5 % target for solvers; they are not physical truth.

### Golden data

A case is `tests/golden/<suite>/<case>/case.toml` with `source`, `[tolerance]`, `[inputs]`,
`[expected]`. `pytest --update-golden` rewrites `[expected]` (the file is regenerated whole, so keep
prose in `source`); list regenerated files in the PR. The directory is found from the working
directory upward, or from `$OA_GOLDEN_DIR`.

### Markers

`golden`, `external(tool)` (skipped when `openems`/`palace`/`kicad`/any named executable is not
found), `slow`, `kicad` (skipped without `kicad-cli`; honours `$KICAD_CLI`).

### Harness

`run_inline` runs `validate_setup`, `plan`, every task in dependency order and `reduce` in the
calling thread with an in-memory `RunContext` (scratch directory, in-memory artifact store,
services from the registry or a `services=` mapping, optional `geometry=` factory). It also checks
the plan contract: unique ids, known dependencies, no cycles, and JSON round trips of `Task` and
`TaskOutput`. There is no cache and no process isolation; `oa_jobs` provides those.

## Fixture files

`python -m oa_testkit.fixtures_dump` writes `tests/fixtures/model/*.oab.json`,
`tests/fixtures/xsection/*.json` and `tests/fixtures/em/*.json`; `--check` fails if they differ
from what the builders produce.

## Contracts implemented

board-model.md section 14.5 (fixture contract); interfaces.md sections 1 (CrossSection fixtures),
3.3 (harness), 5 (EMProblem fixtures); ARCHITECTURE.md section 8 (markers, golden layout).
