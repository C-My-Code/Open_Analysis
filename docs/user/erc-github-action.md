<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# `oa erc` in GitHub CI (SARIF)

Run the Open_Analysis rule check on every pull request. Findings show up in the PR's "Code
scanning" tab, and the job fails when an error-level rule fires.

Copy [`examples/erc.yml`](examples/erc.yml) to `.github/workflows/erc.yml` in your board
repository and set `BOARD` to your `.kicad_pcb` path. What it does:

1. installs `open-analysis` with `uv tool install` (no KiCad needed: the board file is read directly);
2. runs `oa erc --sarif erc.sarif --fail-on error`, which writes SARIF 2.1.0 and exits 1 if any
   error-severity violation exists (`--fail-on warning` is stricter, `never` always passes);
3. uploads the SARIF with `github/codeql-action/upload-sarif`, also when the check failed;
4. keeps the SARIF as a build artifact.

Without an `oa-project.yaml` next to the board, `oa erc --project board.kicad_pcb` analyses the file
directly with default materials and writes nothing beside it. Commit your own `oa-project.yaml`,
`oa/physics.yaml` and `oa/intent.yaml` (create them with `oa init --yes <dir>`) to get your stackup
materials, target impedances and waivers into CI; the workflow then uses the project directory.

Waivers and per-rule thresholds go in `--rules` (JSON or `@file`), for example
`--rules '{"RP-001": {"severity": "warning"}}'`. List the rule ids with `oa describe --json`.

The ERC result has accuracy class `screening`: treat findings as review prompts. Run the
solver-based analyses locally; CI runners have no openEMS or Palace.
