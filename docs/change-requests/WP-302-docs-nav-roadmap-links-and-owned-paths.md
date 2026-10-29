<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# WP-302: docs nav, ROADMAP links, and the entry point

## Problem

WP-302 owns `tests/golden/crosssolver/**`, `tests/golden/published/**`, `tools/validation/**`
and `docs/validation/**`. The acceptance criteria ask for three things outside those paths:

1. A link from each ROADMAP M1 validation-gate row to its case directory (`docs/ROADMAP.md`).
2. A `Validation` entry in the docs site navigation (`mkdocs.yml`, WP-000).
3. A published console command (`oa validate`), which would live in `oa-cli` (WP-207).

## Proposed change

1. The mapping exists as `docs/validation/gates.md` (generated rows are in
   `docs/validation/*.md`). Add to the ROADMAP gate table a last column "Cases" that links to
   `validation/gates.md#<anchor>`, or have the integrator paste the table.
2. Add `- Validation: validation/index.md` to the `nav` of `mkdocs.yml`. `docs/validation/index.md`
   and the per-area pages are generated and committed; the CI job `.github/workflows/wp-302.yml`
   rebuilds them and uploads the result as an artifact.
3. Keep `uv run python tools/validation/run_validation.py` as the entry point for now. If an `oa
   validate` command is wanted, it must be a thin adapter over `run_validation.main()` in
   `oa-cli`; the tool has no logic that belongs in `oa_service`.

## Impact

Docs and CI only. No contract changes. Without (2) the generated pages are not reachable from the
site navigation.

## Related findings (no contract change)

- `tools/validation` imports test helper modules of other packages (`_xs`, `pi_helpers`,
  `erc_helpers`, `ingest_xval`, `em_helpers`) by path, to reuse the reference cases rather than
  duplicate them. If those helpers move, `val_*.py` must follow.
- The published-reference sets requested by TASKS (Polar SI9000, Wadell, Bryant-Weiss,
  TNT-MMTL/atlc) are `[verify]` slots with no numbers: the sources were not available and no
  value is typed from memory. They need someone with the books or app notes.
