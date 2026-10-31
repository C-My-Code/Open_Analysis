<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# open-analysis

The install package of Open_Analysis. It contains no logic: it depends on every M1 package and
declares the two console scripts.

```text
uv tool install open-analysis     # or: pip install open-analysis
oa --help
oa-mcp --help
```

| Script | Entry point | Purpose |
|---|---|---|
| `oa` | `oa_cli.main:main` | The command line (ERC, impedance, DC IR, EM, reports). |
| `oa-mcp` | `oa_mcp.cli:main` | MCP server for AI agents (stdio by default). |

`pip install "open-analysis[assistant]"` adds the optional in-app AI assistant (`oa-assistant`).

## Public API

`open_analysis.__version__` only. Use the packages it installs for everything else; start with the
[quick-start guide](../../docs/user/quickstart.md).

## Contracts and golden suites

None of its own. The end-to-end contract is `tests/e2e/` (CLI) and `tests/agents/test_m1_workflow.py`
(MCP stdio).
