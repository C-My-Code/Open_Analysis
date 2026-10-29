# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Adapter behaviours: last-resort size cap, progress notifications, budgets, executor default."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp_helpers import BOARD_FILE

from oa_mcp import tools
from oa_mcp.config import Settings
from oa_mcp.server import build_server
from oa_mcp.tools import ToolAdapter, digest
from oa_service import Registry
from oa_service.testing import FakeService

pytestmark = pytest.mark.anyio


def _adapter(service: FakeService, **kw: Any) -> ToolAdapter:
    st = replace(Settings(), **kw)
    return ToolAdapter(Registry.load(service), st.call_context(), st.executor)


async def test_oversize_envelope_is_replaced_by_truncation_error(service: FakeService) -> None:
    adapter = _adapter(service, max_response_bytes=2048)
    spec = adapter._specs["list_nets"]
    big = {"schema": "oa.net_page/1", "version": "1", "ok": True, "data": {"x": "y" * 5000}}
    out = adapter._bound(spec, big)
    assert out["ok"] is False and out["data"] is None
    err = out["errors"][0]
    assert err["code"] == "RESULT_TRUNCATED" and err["details"]["bytes"] > 2048
    small = {"schema": "s", "version": "1", "ok": True, "data": {"x": 1}}
    assert adapter._bound(spec, small) is small


async def test_progress_notifications_follow_the_running_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(tools, "POLL_S", 0.05)
    svc = FakeService(auto_complete=False, workdir=tmp_path / "slow")
    server = build_server(Registry.load(svc), settings)
    events: list[str | None] = []
    done: list[Any] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        events.append(message)

    async with create_connected_server_and_client_session(server) as c:
        opened = (await c.call_tool("open_board", {"path": str(BOARD_FILE)})).structuredContent
        assert opened is not None
        pid = opened["data"]["project_id"]

        async def run() -> None:
            done.append(
                await c.call_tool(
                    "run_erc",
                    {"project_id": pid, "wait_s": 30},
                    progress_callback=on_progress,
                )
            )

        async with anyio.create_task_group() as tg:
            tg.start_soon(run)
            with anyio.fail_after(10):
                while not events:
                    await anyio.sleep(0.05)
            running = svc.jobs(pid, state="running", limit=1)
            svc.finish_job(running[0].job_id)
    assert events[0] is not None and "erc" in events[0]
    assert done[0].structuredContent["data"]["job"]["state"] == "succeeded"


async def test_executor_default_is_injected_only_where_the_tool_has_it(
    service: FakeService,
) -> None:
    adapter = _adapter(service, executor="inline")
    seen: dict[str, Any] = {}
    reg = adapter._registry
    real = reg.dispatch

    def spy(name: str, args: Any, ctx: Any) -> Any:
        seen[name] = dict(args)
        return real(name, args, ctx)

    reg.dispatch = spy  # type: ignore[method-assign]
    await adapter.call("list_nets", {"project_id": "p_x"})
    await adapter.call("run_erc", {"project_id": "p_x"})
    assert "executor" not in seen["board.nets"]
    assert seen["erc.run"]["executor"] == "inline"


def test_digest_prefers_text_then_job_summary_then_errors() -> None:
    assert digest({"ok": True, "data": {"text": "hello"}}) == "hello"
    nested = {"ok": True, "data": {"job": {"summary": {"text": "from job"}}}}
    assert digest(nested) == "from job"
    bad = {"ok": False, "errors": [{"code": "X", "message": "m", "hint": "h"}]}
    assert digest(bad) == "X: m Hint: h"
