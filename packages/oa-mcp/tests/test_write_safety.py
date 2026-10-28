# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Write tools refuse without enablement and without confirmation."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_fake_service import FakeOaService
from mcp_helpers import BOARD_DIR

from oa_mcp.config import Settings
from oa_mcp.server import build_server

pytestmark = pytest.mark.anyio


async def _call(server: FastMCP, tool: str, **args: Any) -> tuple[dict[str, Any], bool]:
    async with create_connected_server_and_client_session(server) as c:
        res = await c.call_tool(tool, args)
        assert res.structuredContent is not None
        return res.structuredContent, bool(res.isError)


async def test_apply_refused_without_allow_writes(
    service: FakeOaService, settings: Settings
) -> None:
    server = build_server(service, settings)
    env, err = await _call(server, "apply_proposal", proposal_id="prop_x", confirm_token="t")
    assert err and env["errors"][0]["code"] == "PERMISSION_DENIED"
    assert "apply_proposal" not in service.dispatched  # refused before reaching the service


async def test_apply_refused_without_confirm_token(
    service: FakeOaService, settings: Settings
) -> None:
    server = build_server(service, replace(settings, allow_writes=True))
    env, err = await _call(server, "apply_proposal", proposal_id="prop_x", confirm_token="")
    assert err and env["errors"][0]["code"] == "CONFIRMATION_REQUIRED"
    assert service.dispatched == []
    assert service.applied == []


async def test_propose_is_a_dry_run_then_apply_works_once(
    service: FakeOaService, settings: Settings
) -> None:
    server = build_server(service, replace(settings, allow_writes=True))
    async with create_connected_server_and_client_session(server) as c:
        opened = (await c.call_tool("open_board", {"path": str(BOARD_DIR)})).structuredContent
        assert opened is not None
        prop = (
            await c.call_tool("propose_rule_changes", {"project_id": opened["data"]["project_id"]})
        ).structuredContent
        assert prop is not None
        data = prop["data"]
        assert service.applied == []  # proposing never writes

        bad = await c.call_tool(
            "apply_proposal", {"proposal_id": data["proposal_id"], "confirm_token": "nope"}
        )
        assert bad.isError and service.applied == []

        ok = await c.call_tool(
            "apply_proposal",
            {"proposal_id": data["proposal_id"], "confirm_token": data["confirm_token"]},
        )
        assert not ok.isError and service.applied == [data["proposal_id"]]

        again = await c.call_tool(
            "apply_proposal",
            {"proposal_id": data["proposal_id"], "confirm_token": data["confirm_token"]},
        )
        assert again.isError and len(service.applied) == 1
