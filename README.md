<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Open_Analysis

Open_Analysis is an open-source (GPL-3.0-or-later) analysis suite for KiCad printed circuit boards, with Altium boards supported through import. It covers trace impedance, rule and EMI checks, DC IR drop, and 3D full-wave S-parameters. Signal-integrity (SI) channels, AC power integrity (PI), thermal analysis and EMC pre-compliance are planned.

It is built to answer one question before you pay for fabrication or a compliance test: **how likely is this design to meet its spec?** Every result carries an accuracy class and a stated error, so you can judge how far to trust it. See [How to read results](docs/CONFIDENCE.md).

## Status

Status follows [docs/ROADMAP.md](docs/ROADMAP.md). **Available** means the code runs today and its validation results are on the [validation page](docs/validation/index.md). It does not mean every validation case is closed; open gaps are listed in the next section.

| Feature | Status | Notes |
|---|---|---|
| KiCad 7 to 10 board ingest (`.kicad_pcb`), IPC-2581 ingest | Available | Altium and others through `kicad-cli pcb import` |
| Closed-form trace impedance, 2D BEM, 2D FEM reference | Available | `oa impedance xs`, `oa impedance profile` |
| ERC / EMI rule engine (21 rules registered) | Available | One known validation gap, see below |
| DC IR drop (`pi.dc`) | Available | One rail plus return; multi-rail trees are not done |
| 3D S-parameters with openEMS | Available | Needs openEMS installed. Validated 1 to 10 GHz (microstrip) and 4 to 20 GHz (via) only |
| 3D S-parameters with Palace | In progress | Adapter exists. Its validation cases are pending (WP-301) |
| Own GPU FDTD engine (`oa-fdtd`) | In progress | M1P, gated by [BENCHMARKS.md](docs/BENCHMARKS.md); B-PERF-01 has no engine result yet |
| GPU acceleration (`oa-accel`) | In progress | CuPy backend with a CPU fallback |
| Web UI, REST server, MCP server for AI agents | Available (partly) | Several UI views are not done; see [docs/FOLLOWUPS.md](docs/FOLLOWUPS.md) |
| Linear SI channels (IL/RL/TDR), crosstalk | Planned (M2) | |
| AC PDN, thermal, quasi-static 3D extraction | Planned (M2) | |
| EMC pre-compliance (MIL-STD-461, FCC, CISPR, DO-160) | Planned (M2) | [ADR-0011](docs/adr/0011-emc-compliance-prediction.md) |
| Uncertainty quantification, P(pass) | Planned (M2) | [ADR-0012](docs/adr/0012-uncertainty-quantification.md) |
| KiCad live plugin | Planned (M2) | |

The M1 release is held until the M1P benchmark gates pass (ROADMAP, "M1P").

## Why trust it

The approach is to check every solver against something that is not the solver:

1. **Analytic references** (exact formulas, constructions with a known answer).
2. **Published values** from cited papers, books and standards.
3. **Independent solvers** (BEM against FEM, openEMS against our BEM, one ingest reader against another).
4. **Measured data.** None exists yet.

Rules the validation page follows:

- Every number is produced by `tools/validation/run_validation.py` and stored in [`docs/validation/results.json`](docs/validation/results.json). Nothing is typed by hand.
- Cases whose reference came from this project's own code are labelled *self-computed* and are never counted as independent validation.
- Cases whose citation could not be checked are marked `[verify]` and are excluded from pass counts.
- Known gaps are listed, not hidden. They do not count as passed.

Read: the [validation page](docs/validation/index.md) (also as [HTML](docs/validation/validation.html)), the [benchmark gates](docs/BENCHMARKS.md) and the [known gaps](docs/FOLLOWUPS.md).

### Measured accuracy today

From the run of 2026-10-29 (commit `0cc834dc47ea`, Windows 11, Python 3.13.14; 264 cases: 248 pass, 0 fail, 3 known gap, 2 `[verify]`, 11 pending). "Error" is the absolute difference to the reference, as a percentage unless stated.

| Analysis | Reference | Cases passed | Error (max; median) | Open gap |
|---|---|---|---|---|
| Impedance, closed form and BEM | analytic (Cohn, CPW, wire over plane and others) | 59 (45 with an independent reference) | 0.23 %; 0.02 % (Z0 and eps_eff) | none |
| Impedance, BEM vs 2D FEM | cross-solver (shares the quasi-static model) | 123 of 124 | 0.43 %; 0.06 % | eps_eff on narrow high-er microstrip is 0.58 % off against a 0.5 % spec |
| Impedance, published | IPC-2141 microstrip example | 1 independent pass | 0.06 % (36.82 vs 36.8 ohm) | Polar SI9000, Wadell, Bryant-Weiss tables are pending |
| DC IR, analytic | strip, annulus, via barrel, via array, `power_strip` | 6 of 6 | 0.57 % (via barrel, 1 % tolerance); others 0.06 % or less | `kicad-irdrop` cross-check pending |
| DC IR, raster vs FEM | cross-solver | 3 of 4 | 0.68 % to 0.78 % | as above |
| ERC | one synthetic board per rule | 11 of 12 | exact match | IM-002 on three test boards: the boards miss their own impedance targets |
| Ingest | KiCad vs IPC-2581 readers, counts and areas | 33 of 33 | exact | per-net copper-area checks are partial |
| openEMS microstrip Z0 | our BEM, thin-strip copper | pass | -0.43 % (tolerance 3 %) | against BEM with real 35 um copper: +4.1 %, a **known gap** (openEMS uses sheet copper) |
| openEMS insertion loss, 23 mm microstrip, 10 GHz | our BEM RLGC cascade | pass | 0.089 dB (0.78 vs 0.69 dB; tolerance 0.2 dB) | the M1P target is 0.05 dB |
| Palace, openEMS vs Palace via transition | cross-solver | not run | - | pending WP-301 |

What this table does not say:

- Agreement between BEM and FEM tests the numerics, not the quasi-static assumption both share.
- No result has been compared with a physical measurement yet.
- Material data, tolerances and the stack-up are not in these numbers. See [docs/CONFIDENCE.md](docs/CONFIDENCE.md).

## Install

Requirements:

- Python 3.11 or newer. The repository pins no upper bound; the validation run used 3.13.
- [uv](https://docs.astral.sh/uv/).

```text
git clone <this repository>
cd Open_Analysis
uv sync --all-packages
uv run oa --help
```

### Optional: GPU

GPU support is an extra of the `oa-accel` package and is never a hard dependency. Without it everything runs on the CPU.

```text
uv sync --all-packages --extra cuda12      # NVIDIA, CUDA 12 (cupy-cuda12x, nvmath-python)
```

Extras defined in `packages/oa-accel/pyproject.toml`: `cuda12`, `cuda13`, `rocm`. The extra commands above are not verified on a GPU machine here. Check what was found with `uv run oa-accel-doctor`.

AMD ROCm (`amd-cupy`) is **untested on hardware**. Set `OA_DEVICE=cpu` to force the CPU path.

### Optional: external tools

| Tool | Needed for | Notes |
|---|---|---|
| KiCad 10 (`kicad-cli`) | Altium and other import, DRC, optional zone refill | Found automatically in the usual locations (for example `D:\Program Files\KiCad\*\bin` on Windows, `/usr/bin` on Linux), or set in `tools.toml` |
| openEMS (0.37.x) and its Python bindings | `em.sparams --solver openems` | Run as a separate process, never imported. Path comes from `tools.toml` |
| Palace 0.18.x | `em.sparams --solver palace` | Run in WSL2 or Linux. Build with [`docker/palace/install-wsl.sh`](docker/palace/install-wsl.sh) (no sudo, about 40 minutes, 5 GB of disk), or use [`docker/palace/Dockerfile`](docker/palace/Dockerfile) |

Run `uv run oa doctor` for an offline report of what was found (Python, WSL, Docker, KiCad, gmsh and others). On the machine used to check this README, `oa doctor` found KiCad 10.0.5 and gmsh 4.15.2; openEMS was not installed, so the openEMS solve is not demonstrated below.

### Platform notes

- **Windows (native):** the primary development platform. openEMS runs natively. Palace needs WSL2 or Docker. Process memory is capped by a job object (see TASKS.md, rule 10).
- **Linux:** Palace builds natively with the same script. Tests run on Linux in CI.
- **macOS:** supported for the pure-Python core (ROADMAP: macOS CI covers the core). GPU and external-solver paths are not tested there.

## Quick start

All commands below were run on this repository's test boards. `--json` is an option of each subcommand (write `oa erc --json`, not `oa --json erc`). It prints one envelope: `{schema, version, ok, data, warnings, errors}`.

```text
BOARD=tests/boards/kicad10/microstrip/board.kicad_pcb
```

**Open a board and read it**

```text
uv run oa open $BOARD
uv run oa board summary -p $BOARD
uv run oa board nets -p $BOARD
```

`oa open` returns a `project_id` and a summary. A bare board file opens an ephemeral project and writes nothing next to it. Lengths are in metres.

**Impedance**

```text
uv run oa impedance xs --quick '{"topology":"microstrip","w":"5mil","h":"4mil","t":"1.4mil","er":4.2}'
uv run oa impedance profile -p $BOARD --nets '{"names":["MS_250"]}'
```

The first is the `oa describe` example: a closed-form Hammerstad-Jensen microstrip, which prints Z0 = 58.66 ohm. The second samples net `MS_250` along its route and prints a summary with a `result_id`. Check `accuracy_class` before quoting a number.

**ERC**

```text
uv run oa erc -p $BOARD
uv run oa results summary <result_id>
```

The microstrip board gives 0 violations from 21 rules. Results are stored, so `oa results summary` works from a later command. Use `--fail-on error` for a CI exit code.

**DC IR drop** (on the `power_strip` board, a 50 x 5 mm L1 strip, analytic R = 4.926 mohm)

```text
POWER=tests/boards/kicad10/power_strip/board.kicad_pcb
uv run oa run -p $POWER --analysis pi.dc --config '{"rail":{"net":"STRIP"},"vrm":{"component":"J1","vset":1.0},"loads":[{"component":"J2","current":1.0}]}'
```

This reports the worst load drop and the V, dV and J_mag fields. Run `oa describe --analyses` for the full config schema.

**S-parameters** (3D full wave)

```text
uv run oa run -p $BOARD --analysis em.sparams --dry-run --config '{"solver":"openems","region":{"nets":["MS_250"],"reference_nets":["GND"]},"frequency":{"f_min":1e9,"f_max":1e10,"n_points":50}}'
```

`--dry-run` validates the setup and prints an estimate (3 tasks, about 184 s of work, 1.3 GB peak memory, needs `openems-python`). Remove `--dry-run` to run it once openEMS is installed. The solve itself was not run for this README. Frequencies are numbers in Hz in this form. Output is a Touchstone file that passes a passivity, causality and DC quality gate before it is reported as good.

**Discover everything**

```text
uv run oa describe                 # every command, schema id, side effect, example
uv run oa describe --analyses      # installed analyses with config schemas
uv run oa schema                   # published JSON Schema ids
```

### Web UI

```text
uv run oa serve                    # local REST server and web UI; --port, --host, --no-browser
```

The server binds to 127.0.0.1 with a random bearer token. The front end lives in `web/` (see `web/README.md`). Several result views (TDR, Smith chart, 3D preview) are not done, see FOLLOWUPS.

### Connect an AI agent (MCP)

```text
claude mcp add open-analysis -- uv --directory /path/to/Open_Analysis run oa-mcp --root /path/to/projects
```

`oa-mcp` is a thin adapter over the same command catalog as the CLI. It is read-only unless you ask for a `write` command, and writes go through propose then apply. It makes no network calls; only `oa-assistant` (off by default) may. Details for Claude Code, Claude Desktop and others: [docs/agents/connect.md](docs/agents/connect.md). How an agent should work and state accuracy: [docs/agents/AGENTS.md](docs/agents/AGENTS.md), [docs/agents/accuracy.md](docs/agents/accuracy.md).

## Project layout

```text
packages/    Python packages (uv workspace), layered per docs/ARCHITECTURE.md
  oa-model, oa-framework, oa-geometry, oa-physics, oa-mesh      model and core services
  oa-ingest-kicad, oa-ingest-ipc2581, oa-kicad-bridge           board readers
  oa-impedance, oa-erc, oa-pi, oa-si, oa-thermal, oa-qs, oa-emi analyses (some are skeletons)
  oa-em, oa-em-openems, oa-em-palace, oa-fdtd, oa-accel         3D solvers and GPU backend
  oa-network, oa-results, oa-jobs, oa-service                   S-parameter tools, results, jobs, command catalog
  oa-cli, oa-server, oa-mcp, oa-assistant                       adapters (CLI, REST, MCP, optional AI assistant)
  oa-native, oa-testkit, oa-devguard                            Rust core, test fixtures, dev memory guard
web/         web UI
crates/      Rust sources for oa-native
schemas/     published JSON Schemas
tests/       tests, golden data (tests/golden), test boards (tests/boards)
docs/        architecture, specs, ADRs, roadmap, validation, agent docs
tools/       validation, benchmark and board-generation scripts
docker/      Palace build files
```

Key documents: [ARCHITECTURE](docs/ARCHITECTURE.md), [ROADMAP](docs/ROADMAP.md), [TASKS](docs/TASKS.md), [BENCHMARKS](docs/BENCHMARKS.md), [specs](docs/specs/), [ADRs](docs/adr/).

## Contributing

Read [CLAUDE.md](CLAUDE.md) (or [AGENTS.md](AGENTS.md)) and [docs/TASKS.md](docs/TASKS.md). The rules are binding: owned paths per work package, frozen contracts, SPDX headers on every new file, and a DCO sign-off on every commit.

```text
uv run pytest -m "not external"
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run lint-imports
uv run reuse lint
```

## License

GPL-3.0-or-later ([LICENSE](LICENSE), [ADR-0001](docs/adr/0001-license-gpl3.md)). The JSON Schemas (`schemas/`), test boards and fixtures (`tests/boards/`, `tests/fixtures/`) and `examples/` are dual-licensed `CC-BY-4.0 OR GPL-3.0-or-later`: recipients choose either (ADR-0001, `REUSE.toml`). Code stays GPL-3.0-or-later.
