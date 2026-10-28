# oa-mcp

MCP server (`oa-mcp`) that exposes Open_Analysis to AI agents. It implements [ai-integration.md](../../docs/specs/ai-integration.md) section 8 (WP-210) as a **thin adapter** over `oa-service` (ADR-0007): it contains no analysis logic.

User guide: [docs/agents/connect.md](../../docs/agents/connect.md). Agent guide: [docs/agents/AGENTS.md](../../docs/agents/AGENTS.md).

## Public API

- Console script `oa-mcp` (also `python -m oa_mcp`): stdio by default, `--http [--port N] [--token-file F]` for streamable HTTP on `127.0.0.1:/mcp` with bearer token and loopback-only `Origin`.
- Flags: `--root` (repeatable), `--allow-writes`, `--max-runtime`, `--max-memory`, `--max-cpus`, `--no-external`, `--executor`, `--max-response-kb`, `--service-factory MODULE:FUNC`. The `[mcp]` table of `open_analysis/config.toml` supplies the same settings; flags win.
- `oa_mcp.server.build_server(service, settings) -> FastMCP`.
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

## Assumptions about `oa-service` (to reconcile)

`oa-mcp` was written before `oa-service` existed. It uses `OaServiceLike` (`oa_mcp/protocol.py`), tested against `tests/mcp_fake_service.py`. The real `Registry`/`OAService` needs a small shim (or `oa-mcp` switches to it directly) for these points:

1. **Spec objects.** `service.specs()` returns objects with `name, mcp_tool, title, description, side_effect, idempotent, long_running` (the `CommandSpec` fields). Real `Registry.specs()` already fits.
2. **Schemas as JSON.** `input_schema(name)` / `output_schema(name)` return JSON Schema dicts. The real `Registry` has `input_type(name)`/`output_type(name)`, so the shim is `type.model_json_schema()`. `output_schema` is the schema of `Envelope.data`, not of the envelope.
3. **`dispatch` returns a dict** (`Envelope.model_dump(mode="json")`, model key order) and never raises. Real `dispatch` returns an `Envelope`.
4. **Call context.** `oa_mcp.protocol.McpCallContext` is a dataclass with the `CallContext` fields (`roots` as `str`, `allow_writes`, `max_response_bytes`, `caller="mcp"`), budgets as plain dicts with the section 10.4 field names, plus an extra `progress(fraction, message)` callback. Real `CallContext` has `Budget` models and **no progress hook**: WP-207 needs one (or a job-event subscription) for MCP progress notifications.
5. **Command names** are exactly the section 2.4 table (`board.open` ... `propose.apply`), and every `mcp_tool` listed there is non-null. `build_server` fails fast with `ContractError` if a tool of the contract is missing.
6. **Validation errors** (unknown top-level field, bad type) come back from `dispatch` as `ok=false` with `SETUP_INVALID`, a JSON Pointer `path` and a `hint`. The adapter does no JSON-Schema validation of its own (`validate_input=False`).
7. **Waiting.** `wait_s` is an input field handled inside `dispatch` (blocks up to `wait_s`). The adapter does not poll.
8. **Resources.** The real service has no resource API. `OaServiceLike` needs `schema_ids()` (ids of section 7.2, listed as concrete resources) and `read_resource(uri, ctx) -> ResourceContent` for `oa://schemas/...`, `oa://analyses`, `oa://rules`, `oa://docs/rules/{id}`, `oa://projects/...` and `oa://results/...` (raising `ResourceNotFound`). These are expected to be built from `meta.schema`, `meta.describe`, `board.*`, `results.*` and `ProjectSession`. Docs resources (`agent-guide`, `llms.txt`, `accuracy`) are bundled in `oa_mcp/data/` (copies made by `tools/agents/sync_docs.py`, checked by a test).
9. **Service factory.** The CLI loads `--service-factory MODULE:FUNC` (default `oa_service:create_service`, a guess) and calls it as `func(roots=(...), executor="process"|"inline")`.
10. **Oversize code.** The cap reuses `RESULT_TRUNCATED` (a warning code in section 6) as an error code, because section 6 has no error code for "response dropped".

## Test fixtures

`tests/mcp_fake_service.py` is an in-memory `OaServiceLike`. It opens `tests/boards/kicad10/microstrip/board.kicad_pcb` (4 copper layers, found by scanning the file) or an `.oab.json`, serves 5 canned ERC violations, and implements propose/apply with a single-use HMAC token. Its output schemas for tools the tests do not exercise are loose. Replace it by `oa_service.testing.FakeService` when WP-207 merges.

## Not done in this slice

- `oa_service.proposals` (`.kicad_dru` reader/writer, `net_settings` editor, diffs, tokens) and the catalog commands `propose.rules`/`propose.apply`: the tools exist and are tested against the fake only.
- MCP elicitation for `apply_proposal` (section 10.3). The confirm token and the client's own tool approval are the current gates.
- `tests/agents/` (schema-description lint over `schemas/`, M1 workflow test, parity with `oa ... --json`, pytest-socket guard) and `llms-full.txt` generation: they need `oa_service`/the CLI. The description lint over tool schemas is done in `tests/test_tools_schema.py`.
- `oa mcp ...` delegation from the CLI (WP-207).
