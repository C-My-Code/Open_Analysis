# WP-207: API reconciliation and local adapters

Status: proposed by WP-207. The specs are not edited; each item is implemented as a local adapter.

## 1. Canonical REST API versus the WP-110 web UI (FOLLOWUPS "Web UI")

**Decision: the spec is canonical** (interfaces.md section 7 plus ai-integration A11/A17). The generated
`web/src/api/openapi.json` is the contract WP-209 builds its client from.

| WP-110 assumption | Canonical | Notes for WP-209 |
|---|---|---|
| `GET /projects/{pid}/board/full` | Kept as an **additive** endpoint returning the board-model JSON | Same content as the MCP resource `oa://projects/{pid}/board` (ai-integration 8.4), so it is justified; large boards should use the Arrow endpoints (`layers/{layer}/geometry`, `tracks`, `drills`), which exist |
| `ProjectSummary{id,name,board_hash,diagnostics}` | Same, plus `ephemeral`, `project_path`, `board_path`, `source_tool` | Superset |
| `NetInfo.length` | `NetInfo.length` (m) as in section 7, plus `via_count`, `diff_partner`, `target_z`, `intent_tags`; total in `X-Total-Count`, next page in `X-Next-Offset` | Catalog commands name it `length_m`; REST follows section 7 |
| Simplified `ViolationSet` (`violations[]`) | `GET /results/{rid}` is the stored manifest; violations come from `GET /results/{rid}/violations` (paged `ViolationBrief`/`ViolationView`) | UI fields map 1:1 (`location` is `location_m`) |
| Single-raster `FieldResult`, `ProfileResult.values` | The real `oa_results` containers: arrays are `$array` refs, fetched from `/results/{rid}/items/{item}/arrays/{name}` (`.npy`) | WP-209 adapts the field/profile views |
| `JobStatus.state` `'done'` | `JobState` is `succeeded` | Rename in the UI |

Other REST decisions: errors are `{"error": ErrorInfo, "errors": [...]}`; `POST /projects/{pid}/jobs`
returns `JobStatus` (budget refusal is 409 `BUDGET_EXCEEDED` with the estimate in `error.details`);
`GET /api/v1/commands` and `POST /api/v1/commands/{name}` (envelope body) serve the web command layer
(ai-integration 11.5); `GET /board/structure3d` needs a `structure3d_glb` service (WP-206) and
otherwise answers 424 `ANALYSIS_UNAVAILABLE`; WS frames are `JobEvent` JSON plus
`{"type":"project_changed","project_id","board_hash"}` after a file-watch reload.

## 2. Contract gaps found while integrating (local adapters, no spec edit)

1. **`ErrorInfo`** lives in `oa_framework.errors` and has no `details`/`source`. `oa_service.errors.ErrorInfo`
   subclasses it and adds `details`. Proposal: move the superset to `oa_model.errors` (A1).
2. **Accuracy/summary types (A4/A5)** are not in `oa_results`: `AccuracyClass` is a 5-value literal on
   `ResultContainer` and `summary()` returns a dict. `oa_service.models.BundleSummary`/`ItemSummary`
   are built from those dicts and capped (8 KiB / 2 KiB). `Remediation` is `{hint, actions[item, field, value]}`,
   not the A7 shape; the service passes it through as JSON.
3. **Run output.** `erc.run`, `impedance.profile` and `analysis.run` all return one `RunResult`
   (`dry_run`, `valid`, `diagnostics`, `estimate`, `job`), not the per-command shapes of ai-integration 8.3;
   `job.summary` carries the `BundleSummary` (violation counts by severity, top violations).
4. **Added catalog commands** beyond 2.4: `project.list|reload|init`, `setups.list|get|validate|put`,
   `jobs.list`, `meta.doctor`. `setups.put` and `project.init` are `write` commands (need `allow_writes` and
   `confirm`/`--yes`).
5. **Error codes added**: `ANALYSIS_UNAVAILABLE` (exit 3, HTTP 424), `BUDGET_CLAMPED` (warning).
6. **`open_analysis.commands`** entry-point group is defined in `oa_service.commands.GROUP_COMMANDS`
   (A13 lists it for `oa_framework.registry`, which does not have it yet).
7. **CLI uses `click`, not `typer`**: typer 0.27 vendors a private click, so catalog commands cannot be
   generated into a typer app. The surface is unchanged.
8. **`oa pi dc`, `oa em`, `oa cache gc`, `oa mcp`** (interfaces.md section 8 sugar) are not implemented:
   `oa run --analysis pi.dc ...` covers the first two; the others need a catalog command.
9. **Test deps.** `fastapi.testclient` needs `httpx`; Starlette now deprecates that pairing, so the tests
   silence the warning instead of adding an `httpx2` dependency.
10. **Import-linter**: `oa_service` is layer L4b between L5 and the analyses; forbidden contracts keep it
    off direct analysis imports and HTTP/LLM SDKs; `oa_service.testing -> oa_testkit` is the one exception.
    `oa_cli` starts the server through `importlib` (siblings may not import each other).
