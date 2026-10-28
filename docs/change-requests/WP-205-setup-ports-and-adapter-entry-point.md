# WP-205: setup ports do not reach `Analysis.plan`; openEMS adapter has no entry point

Status: proposed. Raised by WP-205 (`em.sparams`). No spec was edited.

## 1. `AnalysisSetup.ports` never reaches the analysis

`AnalysisSetup.ports` (board-model.md 12.2, interfaces.md 10) is separate from `config`, but
`oa_jobs.LocalJobService._prepare` (and `oa_testkit.harness.run_inline_full`) only validate
`setup.config` against `Analysis.Config` and call `plan(ctx, cfg)`. The `PlanContext` has no
`setup`. An analysis therefore cannot see the ports of the setup (interfaces.md example
`via-sparams.yaml` puts them next to `config`).

Local behaviour: `EmSparamsConfig` has a `ports` field; the setup's ports must be written inside
`config`. If it is empty, one `trace_end` port per crossed region face is generated.

Proposal: when `Analysis.Config` declares a `ports` field and `setup.ports` is non-empty, the
job service and the harness merge `{"ports": setup.ports}` into the config before validation
(or add `PlanContext.setup`). Impact: `oa_jobs`, `oa_testkit.harness`; additive.

## 2. `oa-em-openems` declares no `open_analysis.em_adapters` entry point

interfaces.md 3.1 lists `openems = "oa_em_openems:OpenEMSAdapter"`; the merged `pyproject.toml`
has none, so `Registry.load().em_adapters` is empty. `oa_em.adapters.load_adapter` falls back to
a built-in name map (imports `oa_em_openems` by string) so the flow works, but the entry point
should be added by the WP-108 owner (and `oa-em-palace` likewise at WP-301).

## 3. Accuracy fields

Same as `WP-105-accuracy-fields-missing.md`: `oa_results` has no `Accuracy`. `em.sparams`
follows the WP-105 local convention: `accuracy_class` (container literal), plus
`ValidityFlag(code="accuracy", message="accuracy_class=3d_full_wave; method=...; validity=...;
quality=...; converged=...; f_min=...; f_max=...")`, `quality_gate` and `converged` flags.
