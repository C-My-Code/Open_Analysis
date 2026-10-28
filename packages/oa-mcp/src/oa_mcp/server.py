# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Build the FastMCP server over an :class:`~oa_mcp.protocol.OaServiceLike`."""

from __future__ import annotations

from typing import Any

import mcp.types as mt
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from oa_mcp.config import Settings
from oa_mcp.contract import TOOLS
from oa_mcp.prompts import register_prompts
from oa_mcp.protocol import OaServiceLike
from oa_mcp.resources import register_resources
from oa_mcp.tools import ToolAdapter

INSTRUCTIONS = (
    "Open_Analysis: PCB signal/power integrity analysis. Start with open_board, then "
    "board_summary. Read results with result_summary and list_violations (paged), never by "
    "dumping everything. Every number carries an accuracy class: report it. Compute tools are "
    "budgeted; use run_analysis(dry_run=true) first for anything expensive. Writes to KiCad "
    "files go through propose_rule_changes and apply_proposal and need user confirmation. "
    "Read the resource oa://docs/agent-guide for the full guide."
)


class ContractError(RuntimeError):
    """The service does not provide a tool that the MCP contract requires."""


def build_server(
    service: OaServiceLike,
    settings: Settings,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    check_contract: bool = True,
) -> FastMCP:
    """Create the server: tools generated from the catalog, plus resources and prompts."""
    ctx = settings.call_context()
    adapter = ToolAdapter(service, ctx)
    if check_contract:
        missing = sorted(set(TOOLS) - set(adapter.tool_names))
        if missing:
            raise ContractError(f"service catalog lacks MCP tools: {', '.join(missing)}")

    server = FastMCP(
        "open-analysis",
        instructions=INSTRUCTIONS,
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=False,
        log_level="WARNING",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        ),
    )

    low = server._mcp_server

    @low.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
    async def _list_tools() -> list[mt.Tool]:
        return adapter.list_tools()

    @low.call_tool(validate_input=False)  # type: ignore[untyped-decorator]
    async def _call_tool(name: str, arguments: dict[str, Any]) -> mt.CallToolResult:
        request_ctx = server.get_context()
        return await adapter.call(name, arguments, request_ctx.report_progress)

    register_resources(server, service, ctx)
    register_prompts(server)
    return server
