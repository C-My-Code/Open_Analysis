# ADR-0007: AI agents are first-class users; one command catalog behind CLI, REST, MCP and an optional assistant

- Status: Accepted (2026-10-27, user direction: "develop this with the intent to also be used with AI")
- Deciders: project owner, architecture owner
- Contract: [specs/ai-integration.md](../specs/ai-integration.md) (includes an addendum to interfaces.md)

## Context
- LLM agents such as Claude Code will drive Open_Analysis the way CI does: they load a board, run impedance, ERC, DC IR and EM, read the results and propose fixes. Agents fail in predictable ways:
  - prose output they must scrape;
  - unbounded payloads that overflow the context window;
  - interactive prompts;
  - unstable ordering;
  - errors without a machine code;
  - results that don't say how accurate they are, so the agent overclaims.
- ARCHITECTURE principle 2 already names agents/MCP as a client of the headless core. However, no contract says how the CLI, the REST server and an MCP server share logic. Without one, three adapters would each re-implement project handling, paging and error mapping, and they would drift apart.
- Users also asked for an in-app assistant that explains results and triages violations. That assistant needs a third-party LLM and network access. The core must stay offline, deterministic and GPL-clean, and it must never send board data anywhere without consent.
- Our solvers produce labelled data for free (cross-section → RLGC). A surrogate model could make impedance sweeps faster, but only if it never hides that it is an approximation.

## Decision
1. **One command catalog.** A new package, `oa-service` (`oa_service`), sits between the framework/jobs layer and the apps.
   - It defines every user-level operation once, as a `CommandSpec`: a typed input model, a typed output model, a side-effect class (`read`, `compute` or `write`) and a description.
   - It owns project sessions, filtering, pagination, summaries for transport, and error mapping.
   - `oa-cli`, `oa-server`, `oa-mcp`, `oa-assistant` and the web UI's typed command layer are adapters over this catalog. They contain no analysis logic.
2. **MCP server `oa-mcp`.** It uses the official Python MCP SDK and supports both stdio and streamable HTTP. HTTP binds to 127.0.0.1 and requires a bearer token. MCP tools, resources and prompts are generated from or backed by the catalog. It sits at L5 next to `oa-cli` and `oa-server`.
3. **Agent-friendly output is a core contract, not a UI concern:**
   - a stable JSON envelope `{schema, version, ok, data, warnings, errors}` for the CLI (`--json`) and MCP;
   - structured `ErrorInfo{code, message, path, hint}`;
   - documented exit codes;
   - no prompts unless `--interactive`;
   - deterministic ordering;
   - `oa describe` / `oa schema` introspection;
   - a `description` with units on every schema field.
4. **Results describe themselves.**
   - Every result payload yields a size-bounded `ResultSummary` (structured facts plus a short text digest).
   - Every result item carries an `Accuracy` record: accuracy class (rule, closed form, 2D field, 3D full wave, surrogate and so on), validated tolerance, and validity range.
   - Provenance includes an inputs hash.
   - Large results are paginated or downsampled, never dumped.
5. **Violations are actionable.** They keep the frozen fields (stable rule id, severity, location, item refs, measured vs limit). They gain a structured `remediation` with typed actions. `fix_hint` stays and mirrors `remediation.summary`.
6. **Safety by construction.**
   - Read-only by default. Writes are limited to the `.oa/` working area: cache, results, exports and backups.
   - Any change to KiCad project files (`.kicad_dru` rules, `.kicad_pro` net classes) is a two-step **propose → apply** flow. The propose step returns a dry-run unified diff. The apply step requires the server to be started with `--allow-writes`, a confirmation token, and a base-file hash that still matches.
   - Agent-started jobs run under a compute **budget** (max runtime, max memory, max CPUs, whether external solvers may run). Jobs are estimated before they start, refused if over budget, and cancelled if they exceed it.
   - The core, `oa-service` and `oa-mcp` make **no network and no LLM calls**. Import-linter forbids `anthropic` outside `oa-assistant`, and the agent test suite runs under a socket guard.
7. **In-app assistant `oa-assistant`.**
   - It is a separate, optional package. Nothing else imports it; `oa-server` mounts its router only through an entry point.
   - It sits behind a provider protocol. Anthropic Claude is the reference provider: the Messages API with client-side tool use over the same catalog tools, default model `claude-opus-5-5`, configurable.
   - Users bring their own API key, stored in the OS keyring.
   - It is **off by default**, and exactly what is sent to the provider is documented.
   - The assistant may read and compute within budget. It may *propose* changes but never apply them; only a human click in the UI applies a proposal.
8. **Surrogates (M3).**
   - A versioned dataset export (`oa-xsdataset/1`, Parquet) pairs canonical cross-sections with solver RLGC labels.
   - A surrogate may register as impedance `method="surrogate"`. It is used only when explicitly enabled and only inside its trained domain. It always reports `accuracy_class="surrogate"` with its held-out error.
   - Rules must not reach an `error` verdict on surrogate values near the limit. Those cases are re-solved with BEM.

## Alternatives considered
- **Agents use the REST API directly.** Rejected as the primary path. REST stays typed for the web UI, but agents work better with MCP's tool discovery, schemas and prompts, and stdio needs no server lifecycle or token handling. The REST API and MCP still share `oa_service`.
- **MCP tools that call the REST server.** Rejected because it adds a hop, a token and a running server for every agent session. In-process dispatch through `oa_service` gives the same behaviour.
- **LLM features inside analyses** (for example, rule explanations generated by a model). Rejected because they would break determinism, offline use and cache keys. Explanations are rule-authored text plus structured facts. Only the optional assistant paraphrases them.
- **Using the hosted MCP connector of the Messages API for the assistant.** Rejected because the connector reaches only public URLs, and `oa-mcp` is local. The assistant runs the tool loop client-side.
- **Wrapping REST responses in the envelope.** Rejected. That would break the generated TS client that WP-110/WP-209 are building. REST keeps raw typed bodies and adopts `ErrorInfo` in its existing error body.

## Consequences
- There is a new package (`oa-service`, owned by WP-207) and two new apps (`oa-mcp` in WP-210, `oa-assistant` in WP-411). The import-linter layers gain an L4b "app services" layer. The integrator updates the root contracts.
- WP-207 must publish the `oa_service` protocol and command catalog early so WP-210 can build against it in parallel.
- In-progress W0 work has small **additive** type changes:
  - `ErrorInfo`;
  - `Diagnostic.hint`/`path`;
  - `Accuracy`;
  - `ResultSummary` and `summary()`;
  - `Provenance.inputs_hash`;
  - `Remediation`;
  - `Budget`;
  - a reserved `"surrogate"` impedance method.

  These are listed in ai-integration.md §13 and are binding on 000A/000B and WP-107. No frozen field is renamed or removed.
- `ResultBundle.summary` stays a `str` (the digest text). The structured bundle summary is `ResultBundle.digest()`, because pydantic cannot have a field and a method with the same name.
- Every analysis and rule WP gains acceptance criteria for summaries, accuracy and remediation.
- M1 exit gains an automated test in which an agent drives the M1 workflow end to end over MCP.
- Agent-facing docs (`llms.txt`, `docs/agents/AGENTS.md`) and contributor-agent docs (`CLAUDE.md`) become maintained deliverables, tested for broken links and stale tool names.
