<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->
# WP-107: workspace configuration the integrator must apply

**Owner:** WP-000 (root `pyproject.toml`, `packages/oa-results/pyproject.toml`, `uv.lock`).
**Raised by:** WP-107. **Status:** open.

1. **mypy scope.** Root `[tool.mypy] files` lists only `oa-results` and `oa-framework`. Add
   `packages/oa-jobs/src` and `packages/oa-jobs/tests`. `mypy packages/oa-jobs/src packages/oa-jobs/tests`
   passes today (non-strict). `oa_jobs` is not under the strict override.
2. **Untyped third-party imports.** `pyarrow`, `meshio` and `psutil` have no stubs. The code uses
   `# type: ignore[import-untyped]`. Alternatively add them to the `skrf` override
   (`ignore_missing_imports`) and drop the comments, or add `types-psutil` to the dev group.
3. **Dependencies.** `oa_results.io` needs `meshio` (VTU) and its tests need `jsonschema` (SARIF
   schema validation). To stay inside WP-107's owned paths they are declared in
   `packages/oa-jobs/pyproject.toml`, together with `psutil`. Move `meshio` to
   `packages/oa-results/pyproject.toml` (import is lazy in `to_vtu`) and `jsonschema` to the dev
   group. `oa-jobs` tests also import `oa_testkit` (already in the dev group).
4. **uv.lock** must be regenerated (`uv lock`); WP-107 did not commit it.
5. **Test markers.** WP-107 uses `external` (WSL and Docker `uname -a` runners) and `slow`
   (the 5 s `max_runtime_s` acceptance test). Both exist.
6. **Vendored schema.** `packages/oa-results/tests/io/data/sarif-schema-2.1.0.json` is the OASIS
   SARIF 2.1.0 JSON Schema, unmodified (source: oasis-tcs/sarif-spec). Check its licence against
   the REUSE policy; `REUSE.toml` has no annotation for it.
