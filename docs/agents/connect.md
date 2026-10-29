# Connecting Claude to Open_Analysis (`oa-mcp`)

`oa-mcp` is an MCP server. It lets Claude Code, Claude Desktop or any MCP client open boards, run analyses and read results. It makes no network connections to the outside and is read-only unless you ask otherwise.

## Install

```text
uv tool install oa-mcp        # or, from a checkout: uv sync
oa-mcp --help
```

From a repository checkout you can also run it with `uv run oa-mcp ...`.

## Claude Code

```text
claude mcp add open-analysis -- oa-mcp --root /path/to/projects
```

From a checkout:

```text
claude mcp add open-analysis -- uv --directory /path/to/Open_Analysis run oa-mcp --root /path/to/projects
```

Project-scoped (`.mcp.json` next to your boards):

```json
{
  "mcpServers": {
    "open-analysis": {
      "command": "oa-mcp",
      "args": ["--root", "."]
    }
  }
}
```

Check it with `/mcp` inside Claude Code. The prompts appear as slash commands (`/mcp__open-analysis__review_high_speed_nets`).

## Claude Desktop

Edit `claude_desktop_config.json` (Settings, Developer, Edit config), then restart Claude Desktop.

macOS / Linux:

```json
{
  "mcpServers": {
    "open-analysis": {
      "command": "oa-mcp",
      "args": ["--root", "/home/me/pcb"]
    }
  }
}
```

Windows (use forward slashes or doubled backslashes; give the full path to the executable if it is not on `PATH`):

```json
{
  "mcpServers": {
    "open-analysis": {
      "command": "C:/Users/me/.local/bin/oa-mcp.exe",
      "args": ["--root", "D:/Projects/pcb"]
    }
  }
}
```

## Flags

| Flag | Default | Meaning |
|---|---|---|
| `--root DIR` (repeatable) | current directory | Directories the server may read and write under `.oa/`. |
| `--allow-writes` | off | Enables `apply_proposal`, the only tool that changes your KiCad files. |
| `--max-runtime 15m` | 4 h | Hard ceiling for any job. Per-call budgets are clamped to it. |
| `--max-memory 16GB` | 32 GB | Memory ceiling. |
| `--max-cpus N` | all | CPU ceiling. |
| `--no-external` | off | Forbid external solver binaries (openEMS, Palace). |
| `--executor process\|inline` | process | Where jobs run. |
| `--max-response-kb 24` | 24 | Largest structured response. |
| `--http`, `--port`, `--token-file` | off | Streamable HTTP, see below. |

The same settings can be put in the `[mcp]` table of `open_analysis/config.toml` (`root = [...]`, `allow_writes = true`, `max_runtime = "15m"`). Flags win.

## Streamable HTTP

```text
oa-mcp --http --port 8765 --token-file ~/.oa-mcp-token
```

The server binds to 127.0.0.1 only, serves `/mcp`, prints the URL (and a random token if you did not give a token file) on stderr, requires `Authorization: Bearer <token>` and rejects requests whose `Origin` is not a loopback host. With `--port 0` it picks a free port and prints it.

```text
claude mcp add --transport http open-analysis http://127.0.0.1:8765/mcp --header "Authorization: Bearer $(cat ~/.oa-mcp-token)"
```

## Letting Claude change your rules

By default Claude can only propose changes. `propose_rule_changes` returns a diff for `.kicad_dru` or `.kicad_pro`; nothing is written. To allow applying, start the server with `--allow-writes`. Even then every apply needs the single-use `confirm_token` from the proposal, and your client will normally ask you to approve the tool call. A backup is stored in `<project>/.oa/backups/`.

## First prompt to try

> Open `board.kicad_pcb`, summarize it, run ERC and tell me the three most important findings with their accuracy.

Agents should read `oa://docs/agent-guide` (also [AGENTS.md](AGENTS.md)).
