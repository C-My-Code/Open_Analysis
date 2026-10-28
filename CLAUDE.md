# CLAUDE.md: guide for coding agents working on Open_Analysis

This file is for agents that change this repository. If you want to *use* Open_Analysis to analyse a PCB, read [docs/agents/AGENTS.md](docs/agents/AGENTS.md).

Open_Analysis is a uv-workspace monorepo (GPL-3.0-or-later) of Python packages under `packages/`, plus `web/`.

## Read first

1. [docs/TASKS.md](docs/TASKS.md): the rules of engagement (binding) and your work package (WP) with its owned paths and acceptance criteria.
2. The contracts you implement: [board-model.md](docs/specs/board-model.md), [interfaces.md](docs/specs/interfaces.md), [ai-integration.md](docs/specs/ai-integration.md), and ADR-0007.
3. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for layering.

## Rules (full text in TASKS.md)

- **Owned paths are exclusive.** Write only inside the paths your WP owns. Reading is unrestricted.
- **Contracts are frozen.** If a spec blocks you, write `docs/change-requests/<WP-ID>-<slug>.md` (problem, proposed change, impact) and build a local adapter. Never edit the spec.
- **Dependencies** go only in your package's `pyproject.toml`. Never commit `uv.lock` changes from a WP branch; the integrator re-locks.
- **Shared root files** (`pyproject.toml`, `.github/workflows/ci.yml`) belong to WP-000 and the integrator. Extra CI goes in `.github/workflows/<wp-id>.yml`.
- **Test helper modules need unique names** (package prefix, for example `geometry_helpers.py`). Tests run with `--import-mode=importlib`; never `from conftest import ...`.
- **Golden data** lives in `tests/golden/<suite>/`, each case with a `case.toml` (`source`, `tolerance`, `inputs`, `expected`). Regenerate only with `--update-golden` and list regenerated files in the PR.
- **Every new source file has an SPDX header**: `SPDX-FileCopyrightText: 2026 Open_Analysis contributors` and `SPDX-License-Identifier: GPL-3.0-or-later`.
- **Commits carry a DCO sign-off** (`Signed-off-by`).
- No `TODO`s in public APIs. Each package README states its public API, contracts and golden suites.

## AI and agent contracts

- Public model fields have a `description` with the unit in `[...]`.
- Errors are `ErrorInfo{code, message, path, hint}`; never raise bare exceptions across a package boundary.
- Every `ResultItem` sets `accuracy`; every `summary()` stays within the size caps.
- User-level operations are `oa_service` commands. CLI, REST and MCP code is a thin adapter with no logic of its own.
- Read-only by default. Only `write` commands touch user files, and only through propose then apply.
- **Never add network or LLM calls outside `oa-assistant`.** Import-linter and a socket guard enforce this.
- Output ordering is deterministic.

## Commands

```text
uv sync
uv run pytest -m "not external"
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run lint-imports
uv run reuse lint
```

All of these must pass before you hand work back. Tests must pass on Windows and Linux.

## Definition of done

Acceptance criteria of your WP pass; `ruff`, `mypy` and `lint-imports` are clean; README updated; SPDX headers present. Do not merge `main` into your branch; the integrator rebases.
