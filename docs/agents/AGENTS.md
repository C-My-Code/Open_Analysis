# Open_Analysis: guide for AI agents

This guide is for agents that **use** Open_Analysis (analysing PCBs). If you are a coding agent working on the repository itself, read the root `CLAUDE.md` instead. The guide is also served by `oa-mcp` as the resource `oa://docs/agent-guide`.

Open_Analysis checks PCB signal and power integrity: impedance, rule checks (ERC), DC IR drop and EM S-parameters. Every operation is one catalog command, reachable as an MCP tool or as `oa <command> --json`. Both give the same data.

## 1. Setup

```text
claude mcp add open-analysis -- oa-mcp --root /path/to/projects
```

Generic `mcpServers` JSON (Claude Desktop and other clients):

```json
{
  "mcpServers": {
    "open-analysis": {
      "command": "oa-mcp",
      "args": ["--root", "/path/to/projects"]
    }
  }
}
```

See [connect.md](connect.md) for Windows paths, `uv run` variants and the HTTP transport. The server only reads and writes inside its `--root` directories.

## 2. The M1 workflow

1. `describe` to list commands, analyses and rules (once per session).
2. `open_board(path)` with a project directory or a board file. Keep the `project_id`.
3. `board_summary(project_id)`. Check `layer_count`, net classes and `diagnostics`.
4. `list_nets(project_id, high_speed_only=true)` to find the nets that matter.
5. `stackup(project_id)` when impedance depends on material values. Look at `physics_source`: `default` means the stackup values are assumed, not measured.
6. `impedance_profile` for the chosen nets, or `compute_xsection_impedance` for a single cross-section.
7. `run_erc(project_id, wait_s=60)`. If it returns a `JobHandle`, poll `job_status(job_id, wait_s=30)`.
8. `result_summary(result_id)` first. Then `list_violations(result_id, severity_min="error")` page by page, and `explain_violation` for the ones you will report.
9. For fixes, `propose_rule_changes`, show the diff to the user, and let the user decide (section 6).

Never page through everything to "see it all". Summaries are written to be enough for a first answer.

## 3. Reading accuracy

Every result item has an `accuracy` block. Quote it with every number.

| `accuracy_class` | Meaning | Typical tolerance tier |
|---|---|---|
| `rule` | Geometric rule check. The geometry is exact, the threshold is a heuristic. | n/a |
| `closed_form` | Analytic formula (Hammerstad-Jensen, Cohn, IPC-2141). | T0/T1 inside the validity range |
| `2d_field` | 2D quasi-TEM field solve of a cross-section. | T0/T1 |
| `2d_sheet` | 2D conduction on copper sheets with via resistors (DC IR). | T0 (DC) |
| `3d_full_wave` | openEMS or Palace full-wave. | T2 cross-solver, gated by `quality` |
| `estimate` | Order-of-magnitude screening. | none; say "estimate" |
| `unknown` | The producer did not say. Treat as unverified. | none |

Rules for not overclaiming:
- `validity` of `extrapolated` or `invalid` means the number is outside the method's range. Say so, and do not present it as a pass or a fail.
- Give `tolerance_rel` with the value: "52.3 ohm, closed form, +/- 2 %".
- `converged: false` or `quality: "fail"` means do not use the numbers, only report the failure.
- Tolerance tiers: T0 analytic <= 0.5 %, T1 published 1-3 %, T2 cross-solver 2-5 %, T3 measured within the stated uncertainty.

## 4. Budgets and safety

- The server is **read-only by default**. Compute tools write only under `<project>/.oa/`.
- Compute is budgeted: default 10 min, 8 GB, and a CPU limit. Use `run_analysis(dry_run=true)` to get a `PlanEstimate` before anything expensive. A job over budget is refused with `BUDGET_EXCEEDED` and the estimate in `details`.
- You may raise the budget per call, but never above the operator's ceiling (it is clamped and a warning is returned). Ask the user before running anything that needs a higher budget.
- `cancel_job` stops a job. Cancelling is always allowed.
- Paths must lie inside the allowed roots, otherwise `PERMISSION_DENIED`.
- Open_Analysis never writes `.kicad_pcb` or `.kicad_sch`. Board edits are described as `board_edit` remediation actions for a human.

## 5. Coordinates and units

- All numbers are SI floats: metres, ohms, seconds, hertz. Field names carry the unit (`length_m`, `z0_ohm`, `f_max` in Hz).
- Inputs accept strings with units: `"5mil"`, `"0.2mm"`, `"10GHz"`, `"25degC"`.
- The board frame is metres, **Y up**, z from the bottom. KiCad's editor is millimetres, **Y down**. Locations carry `kicad_mm: [x, y]` (null when the transform is unknown): use that when you tell the user where to look in KiCad.

## 6. Propose, then apply

1. `propose_rule_changes(project_id, from_violations={result_id, fingerprints})`. It is a dry run and writes nothing. It returns a `ChangeProposal` with unified diffs for `.kicad_dru` / `.kicad_pro`, a `confirm_token` and an expiry (24 h).
2. Show the user the diff and the rationale. Items under `not_applicable` need a human to edit the board.
3. Only after the user agrees, and only if the server was started with `--allow-writes`, call `apply_proposal(proposal_id, confirm_token)`. The token is single use. If a file changed since the proposal, you get `STALE_BASE`: propose again.

Do not call `apply_proposal` to "try it". A backup is made under `.oa/backups/`, but the call changes user files.

## 7. Common errors

| Code | Meaning | Fix |
|---|---|---|
| `SETUP_INVALID` | Bad input. `path` is a JSON Pointer to the field. | Fix that field. `describe` or `oa://schemas/...` shows the schema. |
| `NOT_FOUND` | Unknown project, net or result. `details` may list close matches. | Use a candidate or call `list_nets`. |
| `AMBIGUOUS_REF` | A name matches several items. | Use an id from `details`. |
| `PERMISSION_DENIED` | Path outside the roots, or a write without `--allow-writes`. | Ask the user to change the server flags. |
| `BUDGET_EXCEEDED` | The estimate is over budget. | Reduce the scope, or ask the user. |
| `TIMEOUT` / `CANCELLED` | `wait_s` elapsed or the job was cancelled. | Poll `job_status`. |
| `TOOL_MISSING` | An external solver is not configured. | Tell the user to run `oa doctor`. |
| `CONFIRMATION_REQUIRED` | A write without a valid `confirm_token`. | Run the propose step. |
| `STALE_BASE` | A target file changed after the proposal. | Propose again. |
| `RESULT_TRUNCATED` | Output was capped. | Use `details.next_offset` or narrower filters. |

Errors come back as normal tool results with `isError=true` and an envelope whose `errors` hold `code`, `message`, `path` and `hint`. Read the hint before retrying.
