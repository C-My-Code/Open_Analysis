# oa-mcp

MCP server (`oa-mcp`) that exposes Open_Analysis to AI agents. It implements [ai-integration.md](../../docs/specs/ai-integration.md) section 8 (WP-210) as a **thin adapter** over `oa-service` (ADR-0007): it contains no analysis logic.

User guide: [docs/agents/connect.md](../../docs/agents/connect.md). Agent guide: [docs/agents/AGENTS.md](../../docs/agents/AGENTS.md).

## Public API

- Console script `oa-mcp` (also `python -m oa_mcp`): stdio by default, `--http [--port N] [--token-file F]` for streamable HTTP on `127.0.0.1:/mcp` with bearer token and loopback-only `Origin`.
- Flags: `--root` (repeatable), `--allow-writes`, `--max-runtime`, `--max-memory`, `--max-cpus`, `--no-external`, `--executor`, `--max-response-kb`, `--service-factory MODULE:FUNC`. The `[mcp]` table of `open_analysis/config.toml` supplies the same settings; flags win.
- `oa_mcp.server.build_server(service, settings) -> FastMCP`.
- `oa_mcp.stdio.run_stdio(server)` serves the stdio transport, and `oa_mcp.stdio.private_stdio()` is the context manager behind it (WP-518; see "stdio transport hygiene" below).
- `oa_mcp.protocol.OaServiceLike`: the narrow interface the adapter needs (below).
- SDK: official `mcp` 1.x (`mcp>=1.30,<2`; PyPI also has 2.x, but the spec targets 1.x `FastMCP`).

What it serves: all 19 tools of section 8.3 (`describe`, `open_board`, `board_summary`, `list_nets`, `get_net`, `stackup`, `compute_xsection_impedance`, `impedance_profile`, `run_erc`, `list_violations`, `explain_violation`, `run_analysis`, `job_status`, `cancel_job`, `get_result`, `result_summary`, `export_report`, `propose_rule_changes`, `apply_proposal`), the resources and templates of section 8.4, and the 7 prompts of section 8.5.

Adapter behaviour (the only logic here):
- `tools/list` is generated from `service.specs()` (sorted by tool name). `inputSchema` is the service's input schema; `outputSchema` is the `oa-envelope/1` schema with `data` specialized to the command's output schema (its `$defs` are hoisted).
- Annotations: `read` -> readOnly + idempotent; `compute` -> not read-only, not destructive; `write` -> destructive. All `openWorldHint=false`.
- Each call returns `structuredContent` = envelope and one text block (digest), with `isError = not ok`. Errors never become JSON-RPC errors, except an unknown tool name.
- Defense in depth for `write` commands: refused with `PERMISSION_DENIED` when the server lacks `--allow-writes`, and with `CONFIRMATION_REQUIRED` when `confirm_token` is empty, before the service is called.
- Last-resort size cap: an envelope above `max_response_bytes` is replaced by an `ok=false` `RESULT_TRUNCATED` envelope (the commands' own pagination should make this a no-op).
- `dispatch` runs in a worker thread; progress reported through `ctx.progress` becomes MCP progress notifications.

## Reconciliation with `oa-service` (WP-207)

`oa-mcp` binds directly to `oa_service.Registry` (the earlier local `OaServiceLike` protocol is gone). The earlier assumptions map as follows.

| Assumption | Outcome |
|---|---|
| spec objects | `Registry.specs()` (`CommandSpec`) used as is. |
| schemas as JSON | `registry.input_type(n).model_json_schema(by_alias=True)`; `output_type(n)` in serialization mode, wrapped into the envelope schema. |
| `dispatch` returns a dict | `Registry.dispatch` returns an `Envelope`; the adapter dumps it with `model_dump(mode="json", by_alias=True)`. |
| call context | the real `CallContext` (caller `mcp`, `Budget` models from `oa_jobs`). |
| progress hook | none exists in `oa-service`. Instead the adapter polls `svc.jobs(project_id, state="running")` once a second while a long-running tool blocks and sends MCP progress notifications. Best effort; needs `project_id` in the arguments. |
| `schema_ids()` | `oa_service.schemas.schema_entries(registry)` / `schema_for`. These cover the envelope, `ErrorInfo` and the command schemas only: the seven published `schemas/*.v1.json` documents (board-model, oa-setup, ...) are **not** served as resources yet. |
| `read_resource()` | implemented in `oa_mcp.resources.ResourceReader` from `svc.analyses()/rules()`, `session.board` (`dumps_board`), `session.setup()`, `svc.result()` with `results_view.manifest/item_arrays/export_bundle`. `oa://projects/{pid}/physics` is not available (no path in `ProjectHandle`); `intent` is read from `<root>/oa/intent.yaml` when the service exposes `root_of`. |
| service factory | `--service-factory MODULE:FUNC`, default `oa_service.local:LocalService`, called with no arguments. |
| run output shapes | `RunResult` (`job.result_id`, `job.summary`) as in the WP-207 change request, not the per-tool shapes of section 8.3. |
| paging | inputs use `page: {offset, limit}`; extra tools are exposed too (`list_jobs`, `list_projects`, `reload_project`, `list_setups`, `get_setup`, `validate_setup`, `doctor`). |
| executor | `--executor` fills the `executor` input of the run tools when the call omits it. |

Additive changes outside `packages/oa-mcp` (listed for the integrator):
- `packages/oa-service/pyproject.toml`: entry point `open_analysis.commands` -> `proposals = oa_service.proposals:commands`.
- `oa_service/proposals/**` and `packages/oa-service/tests/proposals/**` (owned by WP-210).
- `propose.rules` is declared `compute` (it stores the proposal under `.oa/proposals/`), not `read` as in the section 2.4 table.
- `oa_service.proposals` reaches `oa_erc.dru.propose_dru` through `importlib` (the registry-only contract forbids a static import); without `oa-erc` installed, `from_violations` returns `ANALYSIS_UNAVAILABLE`.
- `tests/agents/` and `llms-full.txt` at the repo root; `tools/agents/{sync_docs,build_llms,check_docs}.py`. The root `testpaths` must include `tests/agents` (integrator).

Adapter behaviour that is MCP-specific: the last-resort size cap (`RESULT_TRUNCATED`, reused as an error code because section 6 has none for "response dropped"), tool annotations, and elicitation. For `apply_proposal`, when the client supports elicitation the adapter first dispatches without the token to obtain the diff (`CONFIRMATION_REQUIRED` details), asks the user, and only then dispatches the real call; a decline returns `CONFIRMATION_REQUIRED`. Clients without elicitation use the confirm-token flow. The token is an HMAC with a per-process secret and is single use; `confirm=true` is honored only for the CLI and UI callers.

## stdio transport hygiene (WP-518)

The JSON-RPC pipes are moved to private, non-inheritable copies of fds 0 and 1 for as long as the server runs. fd 0, the Win32 `STD_INPUT_HANDLE` and the stdin of child processes become the null device. fd 1, `STD_OUTPUT_HANDLE` and `sys.stdout` become stderr. Without this:

- On Windows, a synchronous handle query on the stdin pipe (`GetFileInformationByHandle`, `PeekNamedPipe`, CRT `fstat`) waits behind the transport's pending `ReadFile` until the client sends another line. Loading gmsh's DLL makes such a query while it holds the loader lock, so `run_analysis(pi.dc)` never returned.
- Output from C libraries, `print` or child processes would corrupt the protocol stream, and a child process that inherited stdin could consume messages.

`python -m oa_mcp` only serves when it is run as `__main__`, so a spawned child that re-imports the module does not start a second server.

## Tests

- `packages/oa-mcp/tests`: in-process MCP client over `FakeService` (tool list and schemas, flow, write safety, elicitation, resources, prompts, progress), stdio (subprocess; also a `NoisyService` that prints to stdout and fd 1, runs child processes and queries the stdin handle, under a 120 s timeout) and streamable HTTP (token, Origin).
- `tests/agents`: the real `LocalService` behind MCP on the WP-102 boards (M1 workflow in-process and over a real `oa-mcp` stdio subprocess, read-only hash check, propose/apply, budgets), CLI parity, schema-description lint with a shrink-only baseline, socket guard, import hygiene, docs checks.

## Not done

- Serving the seven published `schemas/*.v1.json` documents and `oa://projects/{pid}/physics` as resources.
- KiCad acceptance of the patched `.kicad_dru` with `kicad-cli pcb drc` (marker `kicad`).
- `oa mcp ...` delegation from the CLI (WP-207).
