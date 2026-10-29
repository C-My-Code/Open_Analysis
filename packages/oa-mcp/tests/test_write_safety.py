# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Write tools refuse without enablement and without confirmation; elicitation shows the diff."""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import mcp.types as mt
import pytest
from mcp.client.session import ClientSession
from mcp.shared.context import RequestContext
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_helpers import BOARD_FILE

from oa_mcp.config import Settings
from oa_mcp.server import build_server
from oa_service import Registry
from oa_service.testing import FakeService

pytestmark = pytest.mark.anyio

CHANGE = {
    "kind": "custom_rule",
    "name": "oa_demo",
    "condition": "A.NetClass == 'HS'",
    "constraint": "clearance",
    "min": "0.2mm",
}


@pytest.fixture
def board(tmp_path: Path) -> Path:
    p = tmp_path / "board.oab.json"
    shutil.copy(BOARD_FILE, p)
    return p


@asynccontextmanager
async def session(
    service: FakeService, board: Path, *, writes: bool, elicit: Any = None
) -> AsyncIterator[ClientSession]:
    settings = replace(Settings(roots=(str(board.parent),)), allow_writes=writes)
    server = build_server(Registry.load(service), settings)
    async with create_connected_server_and_client_session(server, elicitation_callback=elicit) as c:
        yield c


def errors(res: mt.CallToolResult) -> list[dict[str, Any]]:
    assert res.structuredContent is not None
    out: list[dict[str, Any]] = res.structuredContent["errors"]
    return out


async def propose(c: ClientSession, board: Path) -> dict[str, Any]:
    opened = (await c.call_tool("open_board", {"path": str(board)})).structuredContent
    assert opened is not None
    res = await c.call_tool(
        "propose_rule_changes", {"project_id": opened["data"]["project_id"], "changes": [CHANGE]}
    )
    assert not res.isError and res.structuredContent is not None
    data: dict[str, Any] = res.structuredContent["data"]
    return data


def dru(board: Path) -> Path:
    return board.with_suffix(".kicad_dru")


async def test_apply_refused_without_allow_writes(service: FakeService, board: Path) -> None:
    async with session(service, board, writes=False) as c:
        p = await propose(c, board)
        res = await c.call_tool(
            "apply_proposal",
            {"proposal_id": p["proposal_id"], "confirm_token": p["confirm_token"]},
        )
        assert res.isError and errors(res)[0]["code"] == "PERMISSION_DENIED"
    assert not dru(board).exists()


async def test_apply_refused_without_or_with_wrong_token(service: FakeService, board: Path) -> None:
    async with session(service, board, writes=True) as c:
        p = await propose(c, board)
        for args in ({}, {"confirm_token": "nope"}, {"confirm": True}):
            res = await c.call_tool("apply_proposal", {"proposal_id": p["proposal_id"], **args})
            assert res.isError and errors(res)[0]["code"] == "CONFIRMATION_REQUIRED"
    assert not dru(board).exists()


async def test_propose_is_a_dry_run_then_apply_works_once(
    service: FakeService, board: Path
) -> None:
    async with session(service, board, writes=True) as c:
        p = await propose(c, board)
        assert not dru(board).exists()  # proposing never writes user files
        args = {"proposal_id": p["proposal_id"], "confirm_token": p["confirm_token"]}
        ok = await c.call_tool("apply_proposal", args)
        assert not ok.isError, ok.structuredContent
        assert '(rule "oa_demo"' in dru(board).read_text(encoding="utf-8")
        assert (await c.call_tool("apply_proposal", args)).isError


def _elicitor(accept: bool, seen: list[str]) -> Any:
    async def cb(
        context: RequestContext[ClientSession, Any], params: mt.ElicitRequestParams
    ) -> mt.ElicitResult:
        seen.append(params.message)
        if accept:
            return mt.ElicitResult(action="accept", content={"apply": True})
        return mt.ElicitResult(action="decline")

    return cb


async def test_elicitation_shows_the_diff_and_accept_applies(
    service: FakeService, board: Path
) -> None:
    seen: list[str] = []
    async with session(service, board, writes=True, elicit=_elicitor(True, seen)) as c:
        p = await propose(c, board)
        res = await c.call_tool(
            "apply_proposal",
            {"proposal_id": p["proposal_id"], "confirm_token": p["confirm_token"]},
        )
        assert not res.isError, res.structuredContent
    assert len(seen) == 1 and "oa_demo" in seen[0] and "+(rule" in seen[0]
    assert p["confirm_token"] not in seen[0]
    assert dru(board).exists()


async def test_elicitation_decline_blocks_the_write(service: FakeService, board: Path) -> None:
    seen: list[str] = []
    async with session(service, board, writes=True, elicit=_elicitor(False, seen)) as c:
        p = await propose(c, board)
        res = await c.call_tool(
            "apply_proposal",
            {"proposal_id": p["proposal_id"], "confirm_token": p["confirm_token"]},
        )
        assert res.isError and errors(res)[0]["code"] == "CONFIRMATION_REQUIRED"
    assert not dru(board).exists()
