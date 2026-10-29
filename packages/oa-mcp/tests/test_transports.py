# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""stdio (subprocess) and streamable HTTP (token and Origin guard) transports."""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP
from mcp_helpers import BOARD_FILE, REPO_ROOT

from oa_mcp.http import bind_loopback, load_or_create_token, make_uvicorn, origin_is_loopback

pytestmark = pytest.mark.anyio

TESTS_DIR = Path(__file__).resolve().parent
TOKEN = "t" * 32


async def _flow(session: ClientSession) -> None:
    await session.initialize()
    opened = await session.call_tool("open_board", {"path": str(BOARD_FILE)})
    assert opened.structuredContent is not None
    pid = opened.structuredContent["data"]["project_id"]
    summary = await session.call_tool("board_summary", {"project_id": pid})
    assert summary.structuredContent is not None
    assert summary.structuredContent["data"]["layer_count"] == 2
    erc = await session.call_tool("run_erc", {"project_id": pid, "wait_s": 60})
    assert erc.structuredContent is not None
    rid = erc.structuredContent["data"]["job"]["result_id"]
    res = await session.call_tool("result_summary", {"result_id": rid})
    assert res.structuredContent is not None
    assert "1 error" in res.structuredContent["data"]["text"]


async def test_stdio_end_to_end() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(TESTS_DIR), env.get("PYTHONPATH", "")])
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "oa_mcp",
            "--service-factory",
            "oa_service.testing:FakeService",
            "--root",
            str(REPO_ROOT / "tests"),
        ],
        env=env,
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await _flow(session)


@asynccontextmanager
async def _http_server(server: FastMCP) -> AsyncIterator[str]:
    sock = bind_loopback(0)
    port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}/mcp"
    async with anyio.create_task_group() as tg:
        uv = make_uvicorn(server, TOKEN)
        tg.start_soon(lambda: uv.serve(sockets=[sock]))
        async with httpx.AsyncClient() as probe:
            for _ in range(100):
                try:
                    await probe.get(url)
                    break
                except httpx.TransportError:
                    await anyio.sleep(0.05)
        try:
            yield url
        finally:
            uv.should_exit = True
    sock.close()


async def test_http_end_to_end_with_token(server: FastMCP) -> None:
    async with _http_server(server) as url:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        async with (
            httpx.AsyncClient(headers=headers) as http,
            streamable_http_client(url, http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await _flow(session)


async def test_http_rejects_missing_wrong_token_and_foreign_origin(server: FastMCP) -> None:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    accept = {"Accept": "application/json, text/event-stream"}
    async with _http_server(server) as url, httpx.AsyncClient() as http:
        r = await http.post(url, json=body, headers=accept)
        assert r.status_code == 401
        r = await http.post(url, json=body, headers={**accept, "Authorization": "Bearer wrong"})
        assert r.status_code == 401
        good = {**accept, "Authorization": f"Bearer {TOKEN}"}
        r = await http.post(url, json=body, headers={**good, "Origin": "https://evil.example"})
        assert r.status_code == 403
        r = await http.post(url, json=body, headers={**good, "Origin": "http://localhost:3000"})
        assert r.status_code not in (401, 403)


def test_origin_check() -> None:
    assert origin_is_loopback("http://127.0.0.1:8080")
    assert origin_is_loopback("http://localhost")
    assert origin_is_loopback("http://[::1]:5173")
    assert not origin_is_loopback("https://evil.example")
    assert not origin_is_loopback("http://127.0.0.1.evil.example")
    assert not origin_is_loopback("null")


def test_token_file_and_random_token(tmp_path: Path) -> None:
    f = tmp_path / "tok"
    f.write_text("a" * 20 + "\n", encoding="utf-8")
    assert load_or_create_token(f) == "a" * 20
    f.write_text("short", encoding="utf-8")
    with pytest.raises(ValueError, match="at least 16"):
        load_or_create_token(f)
    assert len(load_or_create_token(None)) >= 32
    assert load_or_create_token(None) != load_or_create_token(None)
