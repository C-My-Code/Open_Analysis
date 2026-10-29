<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# tools/bench (WP-304)

Benchmark harness for the M1P gates in [docs/BENCHMARKS.md](../../docs/BENCHMARKS.md). One case
directory per BENCHMARKS.md id, one runner, one results file per run, and pages generated from
the results (`docs/validation/benchmarks/`, plus a "Performance" section on the validation page).

```text
uv run python tools/bench/run.py --list
uv run python tools/bench/run.py --case B-ERC-01,B-IMP-01           # light cases, profile bench
uv run python tools/bench/run.py --group impedance --device cpu
uv run python tools/bench/run.py --allow-heavy --case B-DC-02        # heavy token holder only
uv run python tools/bench/run.py --check-regression                  # exit 1 on a regression
uv run python tools/validation/build_page.py                         # rebuild the pages
uv run pytest tools/bench -q                                         # harness self-test
```

## What a run does

- Applies the profile first (BENCHMARKS.md section 1): `bench` = 8 threads, 16 GB host cap,
  `OA_GPU_MEM_GB=12`; `default` = 2 threads, 8 GB. The cap is the root `conftest.py` Job Object
  code (`OA_TEST_MEM_GB`), loaded by path, so one implementation exists. Off Windows it falls
  back to `RLIMIT_AS`.
- Refuses to start when the CPU load is above 25 % (`--skip-load-check` records `skipped` in the
  file).
- Runs each runnable sub-case in its own worker process: one cold run, then `--reps` (3) warm
  runs. The gated time is the warm median. Records accuracy, cold and warm times, peak working
  set (`peak_wset` on Windows, `ru_maxrss` on Linux), peak GPU pool for `cuda`, the thread
  count and the machine fingerprint (CPU, cores, RAM, GPU, library versions, commit, dirty).
- Compares with the targets of the case file and writes
  `tests/benchmarks/results/<date>-<commit>-<host>.json` (schema `oa-bench/1`; rerunning on the
  same day and commit merges by case, sub-case and device).
- Only one heavy case runs at a time (workers run sequentially). Heavy sub-cases need
  `--allow-heavy`; without it they are `ready`.

## Status values

`pass` (accuracy, time and memory inside the targets), `fail`, `pending` (solver or runner does
not exist; the file names the owning WP), `ref-pending` (the reference is not produced yet),
`verify` (reference unchecked, not gating), `ready` (a runner exists; not run in this batch),
`skipped` (for example `--device cuda` with no GPU: `skipped (no device)`). Only `pass` counts.
A case is `pass` only when every sub-case passes, otherwise `partial` or the least advanced
sub-case status.

## Case file (`tests/benchmarks/cases/<ID>/case.toml`, schema `oa-bench-case/1`)

`id`, `title`, `group`, `spec`, `targets_text` (BENCHMARKS.md section 2, verbatim), `[reference]`
(`class`, `citation`, `data` = path under `tests/benchmarks/references/` or an existing golden
suite), and `[[sub]]` entries: `id`, `title`, `runner` (a key of `bench_cases.RUNNERS`),
`heavy`, `pending`/`owner`/`reason` (for sub-cases that cannot run), `devices`, and
`[sub.targets.<device>]` with `accuracy_max`, `accuracy_unit`, `time_max_s`, `time_per`
(`run` or a unit such as `section`), `mem_max_mb`, `gpu_mem_max_mb`. An absent target is not
gated. Targets may be tightened freely; loosening needs an ADR amendment (BENCHMARKS.md section 5).

## Runners (`bench_cases.py`)

A runner takes the device and returns `accuracy`, `units`, optionally `time_s` (its own timing
of the measured core) and `detail`. It reuses the validation helpers (WP-302) for boards,
expected files and reference data. Today: `imp01_sections`, `dc01_power_strip`, `erc_combined`,
`ing_wp102`, `ing_cm5`, `ing_jetson`, `dc02_scale_3e5`, `dc02_scale_1e6` (the last four are
heavy, run with `--allow-heavy`; the DC scale runners are untested at full size).

## Regression alert

`--check-regression` compares with the most recent passing record per case, sub-case and device.
It fails on more than 20 % slower or an accuracy loss of more than 25 % of the tolerance.
`.github/workflows/bench.yml` runs it nightly on a self-hosted runner and builds the pages in CI
from the committed results.

## Known gaps

- No py-spy flamegraphs and no `tests/benchmarks/BASELINE.md` yet (needs the full baseline run
  under the token).
- Not yet implemented: the information-only run under the `default` profile, an `AccelReport`
  record (oa-accel is WP-503), Linux cgroup `memory.peak`, and the cases listed `pending`.
