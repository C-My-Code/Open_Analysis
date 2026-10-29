<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# tools/validation (WP-302)

One runner executes every reference case and writes one JSON file; one generator turns that file
into the published page `docs/validation/`. Nothing on the page is typed in by hand.

```text
uv run python tools/validation/run_validation.py --page            # everything, then the page
uv run python tools/validation/run_validation.py --quick --skip-openems --suites erc,dcir,ingest
uv run python tools/validation/build_page.py                       # page only, from results.json
uv run pytest tools/validation -q                                  # self-test of the tool (fast)
```

Options: `--suites impedance,erc,dcir,ingest,em`, `--quick` (skips the 50-structure BEM-vs-FEM
sweep), `--skip-openems`, `--openems-root DIR` (or `OA_VALIDATION_OPENEMS_ROOT`; the directory
that holds `.oa/tools/openems`), `--out`. Exit status 1 when any case fails; `known_gap` cases are
documented shortfalls (see `docs/FOLLOWUPS.md`) and do not fail the run. The runner sets thread
caps (`OMP/OPENBLAS/MKL_NUM_THREADS=2`, `OA_NUM_THREADS=4` for openEMS) and never starts WSL,
Palace or a network connection.

## Results file

`docs/validation/results.json` (`schema: oa-validation-results/1`): run metadata (commit,
platform, runtime per suite), `summary` per category and `cases[]`. Each case:

| Field | Meaning |
|---|---|
| `id`, `category`, `title` | stable id (`bem.cohn.stripline.w0p1.er1`), area/sub-category, text |
| `source` | citation or `analytic: <formula>`; cases whose citation is unchecked start with `[verify]` |
| `reference_class` | `analytic`, `published`, `cross-solver`, `measured` or `self-computed` |
| `independent` | `false` when the reference came from this project's own code (never counted as independent validation) |
| `quantity`, `unit`, `expected`, `got`, `error_pct`, `error_abs` | the comparison |
| `tolerance` | `{kind: rel|abs|min|max|exact, value, text}` |
| `status` | `pass`, `fail`, `known_gap`, `verify`, `pending`, `skipped` |
| `case_dir`, `note`, `runtime_s` | where the reference data lives, remarks, wall time |

Only `pass` counts as passed. `verify`, `pending`, `skipped` and `known_gap` never do.

## Suites

| Module | Category | What it runs |
|---|---|---|
| `val_impedance.py` | `impedance/exact`, `impedance/published`, `impedance/bem-vs-fem` | closed forms and BEM against Cohn, CPW, edge-coupled stripline, coax, wire, two-wire exact solutions; HJ/Wheeler locks (self-computed); IPC-2141 legacy; BEM vs FEM on the oa_testkit fixtures and on 50 generated PCB structures |
| `val_dcir.py` | `dc-ir`, `dc-ir/cross-solver` | strip, annulus, via barrel and via array analytic cases, WP-102 boards vs `expected.yaml`, FEM vs raster FVM |
| `val_erc.py` | `erc` | all 11 WP-102 boards against `expected.yaml: erc` (rule id, nets, layer, location within 50 um; no extras, no forbidden rules, negative cases) |
| `val_ingest.py` | `ingest` | WP-208 harness: KiCad reader vs `expected.yaml`, IPC-2581 reader vs `expected.yaml`, KiCad vs IPC-2581 |
| `val_em.py` | `em` | openEMS microstrip thru vs the BEM RLGC cascade; Palace and tutorial slots (pending) |

Reference data: `tests/golden/published/` (cited published references and `[verify]` slots),
`tests/golden/crosssolver/` (cross-solver gates and slots), plus the existing golden suites,
which are reused and not copied. Test helpers of the packages (`_xs`, `pi_helpers`,
`erc_helpers`, `ingest_xval`, `em_helpers`) are imported from their test directories, so a
reference case lives in one place only.

## Adding a case

1. Put the reference in a `case.toml` (`source`, `tolerance`, `inputs`, `expected`, and a `[meta]`
   with `reference_class`, `independent`, `citation_status`). Cite exactly, or write
   `[verify]` and the case is excluded from pass counts. Do not enter a number you cannot trace.
2. Add the comparison to the matching `val_*.py` as a `Case(...)`.
3. A reference produced by our own code is `self-computed`, `independent = False`.

## Honesty notes

See the "Known limits" section of the generated `docs/validation/index.md`. In short: the
Wadell, SI9000, Bryant-Weiss and TNT-MMTL/atlc sets are slots without numbers; Palace is pending
WP-301; the openEMS tutorials and the kicad-irdrop/padne oracle are not run; the HJ rows are
self-computed.
