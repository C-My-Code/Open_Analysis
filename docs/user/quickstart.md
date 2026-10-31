<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Open_Analysis quick start

Open_Analysis checks a KiCad board for signal, power and RF integrity problems. It runs on your
own machine, reads your `.kicad_pcb` file, and never changes it. This guide takes you from install
to your first results, then shows how to let an AI agent drive the same analyses.

Every command below was run against the boards in `tests/boards/kicad10/`. Commands print human
text by default; add `--json` (or set `OA_OUTPUT=json`) for one JSON object on stdout.

## 1. Install

You need Python 3.11 or newer and [uv](https://docs.astral.sh/uv/). On Linux, the Gmsh mesher
also needs a few system libraries (Debian/Ubuntu shown; Windows and macOS need nothing extra):

```text
sudo apt-get install libglu1-mesa libxcursor1 libxft2 libxinerama1
```

```text
uv tool install open-analysis
oa --version
oa doctor
```

`oa doctor` works offline. It lists the platform, the external tools it found (KiCad, openEMS,
Palace, Gmsh, WSL, Docker) and what is missing, with hints.

Working from a checkout of the repository instead:

```text
uv sync
uv run oa doctor
```

Two commands are installed: `oa` (the command line) and `oa-mcp` (the server for AI agents, see
section 6). Open_Analysis makes no network connections of its own (only the optional in-app
assistant does).

## 2. KiCad

Open_Analysis reads `.kicad_pcb` files (KiCad 9 and 10) directly, so the analyses in sections 3 and
4 need no KiCad installation. Install [KiCad 10](https://www.kicad.org/download/) if you also
want:

- zone refill before an analysis (for boards saved with unfilled copper pours);
- import of Altium and other foreign formats, and IPC-2581 or ODB++ export;
- the KiCad-dependent tests of the repository.

`oa doctor` shows the `kicad-cli` it found. To point at another install, set `KICAD_CLI` and
`KICAD_PYTHON`, or add a `[tools.kicad]` entry to `tools.toml` (below).

## 3. First analyses

Take a copy of your board in its own directory (the examples use `combined`, a synthetic 8-layer
board from the repository tests):

```text
mkdir my-board
cp tests/boards/kicad10/combined/board.kicad_pcb my-board/
cd my-board
oa init --yes .
```

`oa init` writes `oa-project.yaml`, `oa/physics.yaml` (materials) and `oa/intent.yaml` (which nets
are high speed, target impedances). It never overwrites existing files. Edit those two files to
describe your design; the analyses use them.

Read the board:

```text
oa ingest board.kicad_pcb
```

### Rule check (ERC) with SARIF output

```text
oa erc --sarif erc.sarif --fail-on never
```

This runs the geometry rules (return paths, impedance, differential pairs, vias, power delivery,
RF). The summary says how many violations it found and the accuracy class (`screening`: documented
default thresholds). `--sarif` also writes a SARIF 2.1.0 file for GitHub code scanning or any SARIF
viewer. `--fail-on error` (or `warning`) makes the exit code 1 when violations of that severity
exist, which is what you want in CI (see [erc-github-action.md](erc-github-action.md)).

Page through the violations of a result with the id printed by the run:

```text
oa results violations <result_id> --limit 20
oa results explain <result_id> <fingerprint>
```

### Impedance profile

```text
oa impedance profile --nets '{"names": ["USB_D*"]}'
```

`--nets` takes a selector: `names` (globs), `net_classes`, `diff_pairs` or `kinds`
(`signal`, `power`, `ground`). `--method` is `auto` (default), `closed_form` or `bem`. The result
reports Z0 or Zdiff along the net, with the cross-section method used and its accuracy class.

For one cross-section without a board see `oa impedance xs --help`.

### DC IR drop

Name the rail net, the source component and the loads with their currents:

```text
echo '{"rail": {"net": "+1V0"}, "vrm": {"component": "U5"}, "loads": [{"component": "U1", "current": 1.0}], "temperature": "20degC"}' > dc.json
oa run --analysis pi.dc --config @dc.json
```

(That net and those components belong to `tests/boards/kicad10/power_plane`.) Use
`oa run --analysis pi.dc --config @dc.json --dry-run` first to validate the setup and see the time
and memory estimate. The result gives the worst load drop, via currents and the voltage and
current-density fields, with accuracy class `validated`.

### Report

```text
oa results export <result_id> --format html --out-path report.html
```

Other formats: `sarif`, `csv`, `json`, `md`, `touchstone` (EM results) and `vtu` (fields for
ParaView). Without `--out-path` the file goes to `.oa/exports/<result_id>/` in the project.

### Browser UI

```text
oa serve
```

starts the local web UI and REST server on a loopback port and opens your browser.

## 4. Full-wave EM: openEMS and Palace

`em.sparams` extracts S-parameters of a cut-out of the board with a full-wave solver. It needs an
external solver. Without one, `--dry-run` still validates your setup and prints the estimate.

Describe the region (model coordinates in metres, y pointing up, so `y = -y_file`), the nets and
the frequencies:

```text
echo '{"region": {"nets": ["MS_500"], "bbox": [0.0125, -0.0335, 0.0225, -0.0265]}, "frequency": {"f_min": 0, "f_max": 5e9, "n_points": 11}}' > em.json
oa run --analysis em.sparams --config @em.json --dry-run
oa run --analysis em.sparams --config @em.json --timeout 1200
```

(That region cuts 10 mm out of a microstrip of `tests/boards/kicad10/microstrip`.) The region must
cross the net, or you must add `--ports`.

### openEMS (Windows, Linux, macOS)

Install openEMS 0.37 or newer (<https://openems.de>) together with its Python bindings. The
detailed Windows steps, with checksums, are in
[packages/oa-em-openems/README.md](../../packages/oa-em-openems/README.md). `oa` finds it through,
in order: `OA_OPENEMS_PYTHON` and `OA_OPENEMS_HOME`, `tools.toml`, a project-local `.oa/tools/openems`,
or `openEMS` on `PATH`.

`tools.toml` lives in `%APPDATA%\open_analysis\` (Windows) or `~/.config/open_analysis/`
(Linux, macOS), or wherever `OA_TOOLS_TOML` points:

```toml
[tools.openems-python]
path = "C:/tools/openEMS/venv/Scripts/python.exe"
home = "C:/tools/openEMS"
```

### Palace (Linux, or WSL on Windows)

Palace has no binary release; build it from source. In WSL the script
`docker/palace/install-wsl.sh` of the repository does it without sudo. A Docker image recipe is in
`docker/palace/Dockerfile`. Register it:

```toml
[tools.palace]
runner = "wsl"
distro = "Ubuntu"
path = "/home/<user>/opt/palace/bin/oa-palace-run"
```

Details: [packages/oa-em-palace/README.md](../../packages/oa-em-palace/README.md). Run
`oa doctor` after editing `tools.toml` to confirm both tools are found.

## 5. Reading results honestly

Every result item carries an accuracy class: `reference`, `validated`, `screening`, `estimate` or
`unverified`. A `screening` ERC finding means "look at this", not "this fails". Solver results come
with validity flags and a quality verdict (passivity, convergence). See
[docs/agents/accuracy.md](../agents/accuracy.md) for what each class promises.

## 6. Using it from an AI agent (MCP)

`oa-mcp` is an MCP server (stdio by default). It is read-only unless started with `--allow-writes`,
and only opens boards under the `--root` directories.

Claude Code:

```text
claude mcp add open-analysis -- oa-mcp --root /path/to/projects
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "open-analysis": { "command": "oa-mcp", "args": ["--root", "/path/to/projects"] }
  }
}
```

Then ask, for example: "Open `my-board/board.kicad_pcb`, profile the impedance of the USB pair,
run ERC and tell me what to fix first." The agent calls `open_board`, `impedance_profile`,
`run_erc`, `result_summary` and `list_violations`, and quotes the accuracy class of each answer.
The same flow runs in the test `tests/agents/test_m1_workflow.py` and returns exactly what the
command line gives. More client setups: [docs/agents/connect.md](../agents/connect.md); the tool
list and prompts: [docs/agents/AGENTS.md](../agents/AGENTS.md).

## Troubleshooting

- `oa doctor` first: it reports missing tools with a hint.
- `IMPEDANCE_UNSUPPORTED ... no closed-form model`: use `--method auto` or `bem`.
- `EM.NO_PORTS`: the region does not cross the net; move the `bbox` or pass `--ports`.
- Set `OA_DEVICE=cpu` to keep the compute off the GPU.
