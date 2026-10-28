# oa-service

The single command catalog behind the `oa` CLI, the REST server, the MCP server (WP-210), the web
command layer and the assistant (ADR-0007, `docs/specs/ai-integration.md` section 2). Adapters
contain no logic: they call `Registry.dispatch(name, args, ctx)` and render the `Envelope`.

Layer L4b. It imports L0-L3 and reaches analyses and rules only through the plugin registry
(import-linter enforces both).

## Public API

```python
from oa_service import Registry, CallContext
from oa_service.local import LocalService          # real ingest, jobs, results
from oa_service.testing import FakeService         # in-memory; needs `oa-service[testing]`

svc = LocalService()                                # or FakeService()
reg = Registry.load(svc)                            # built-ins + `open_analysis.commands` entry points
env = reg.dispatch("board.open", {"path": "board.kicad_pcb"}, CallContext(caller="cli"))
env.ok, env.data, env.warnings, env.errors          # Envelope (never raises)
```

| Name | What |
|---|---|
| `Registry` | `specs()`, `definition(name)`, `input_type/output_type`, `dispatch`, `call_json` |
| `CommandSpec` | name, `mcp_tool`, `cli` path, side effect, schema ids, tested `examples` |
| `CommandDef` | spec + typed input/output model + handler `(svc, args, ctx) -> OAModel \| Outcome` + `cli_args` |
| `CallContext` | caller, `roots`, `allow_writes`, `budget_ceiling`, `default_budget`, `max_response_bytes` |
| `Envelope[T]` | `{schema, version, ok, data, warnings, errors}` |
| `ErrorInfo` | `{code, message, path, hint, details}`; `exit_code_for`, `http_status_for` |
| `Page[T]`, `PageRequest` | offset/limit paging with a documented total order |
| `OAService`, `ProjectSession` | the protocols both implementations satisfy |

Third parties (WP-210 `propose.*`) add commands with the entry-point group
`open_analysis.commands`: each entry point loads a `CommandDef` or a callable returning an iterable
of them.

## Catalog

`meta.describe`, `meta.schema`, `board.open`, `project.list`, `project.reload`, `board.summary`,
`board.nets`, `board.net`, `board.stackup`, `impedance.xs`, `impedance.profile`, `erc.run`,
`analysis.run`, `setups.list|get|validate|put`, `jobs.list|status|cancel`,
`results.get|summary|violations|explain|export`. `oa describe --json` is the authoritative list.

Run commands (`erc.run`, `impedance.profile`, `analysis.run`) all return `RunResult`
(`dry_run`, `valid`, `diagnostics`, `estimate`, `job`). `job.summary` holds the bounded
`BundleSummary` when the job finished within `wait_s`. Deviation from the MCP table in
ai-integration 8.3 (which returns per-command shapes): one shape keeps adapters trivial; see
`docs/change-requests/WP-207-api-reconciliation.md`.

## Behaviour that adapters rely on

- **Errors, never exceptions.** Pydantic validation errors become `SETUP_INVALID` with a JSON-Pointer
  `path` and a `hint`; unknown names return `NOT_FOUND` with `details.candidates`.
- **Writes** need `ctx.allow_writes` (else `PERMISSION_DENIED`) and an explicit `confirm=true`
  (else `CONFIRMATION_REQUIRED`).
- **Roots.** Every path argument is resolved and must lie inside `ctx.roots` (empty = unrestricted).
- **Budgets.** Per-call budgets are clamped to `ctx.budget_ceiling` (warning `BUDGET_CLAMPED`);
  estimates over budget fail with `BUDGET_EXCEEDED` and the estimate in `details`.
- **Size caps.** For `mcp`/`assistant` callers a page-shaped payload larger than
  `max_response_bytes` is cut on an item boundary with `RESULT_TRUNCATED` and `details.next_offset`.
  A `BundleSummary` is at most 8 KiB, an `ItemSummary` 2 KiB, inline arrays at most 1000 elements.
- **Determinism.** Lists have total orders (nets by name; violations by severity, rule, nets, x, y,
  fingerprint; jobs newest first). `mask_volatile` masks the volatile fields of section 3.5.

## FakeService (the WP-210 test double)

`FakeService` shares `ServiceBase` with the real service, so board views, paging, summaries, errors
and size caps are identical. Only project loading and job execution are replaced:

- `open_fixture("microstrip" | "stripline" | "diff_pair" | "cpwg" | "split_plane" | "via_transition" | "power_plane")`;
  or `open()` a real `.oab`/`.oab.json` file.
- Jobs finish immediately with canned bundles (`erc` -> three violations IM-001/RP-001/DP-003).
  `FakeService(auto_complete=False)` keeps jobs `running` until `finish_job(id)` (polling, cancel).
  `estimate_seconds=` provokes `BUDGET_EXCEEDED`.
- Registered fake analyses: `erc`, `impedance.profile`, `dummy.work` (config validation is real).

## Schemas

`python -m oa_service.schemas schemas` regenerates `schemas/oa-envelope.v1.json`,
`schemas/oa-error.v1.json` and `schemas/commands/*.json`; `--check` fails when out of date (CI).

## Contracts implemented

ai-integration.md sections 2-4, 6, 9, 10 as far as they concern the service; interfaces.md section 7
(see `docs/change-requests/WP-207-api-reconciliation.md` for the decisions).
