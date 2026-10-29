# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""End-to-end flow through an in-process MCP client over the FakeService."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp.client.session import ClientSession
from mcp_helpers import BOARD_FILE

from oa_service.testing import FakeService

pytestmark = pytest.mark.anyio


async def call(client: ClientSession, tool: str, **args: Any) -> tuple[dict[str, Any], bool]:
    res = await client.call_tool(tool, args)
    assert res.structuredContent is not None
    assert res.content and res.content[0].type == "text"
    return res.structuredContent, bool(res.isError)


async def test_open_summarize_erc_and_read_summary(client: ClientSession) -> None:
    env, err = await call(client, "open_board", path=str(BOARD_FILE))
    assert not err and env["ok"] is True
    pid = env["data"]["project_id"]

    env, err = await call(client, "board_summary", project_id=pid)
    assert not err and env["data"]["layer_count"] == 2

    env, err = await call(client, "run_erc", project_id=pid, wait_s=60)
    assert not err
    job = env["data"]["job"]
    assert job["state"] == "succeeded"
    rid = job["result_id"]

    env, err = await call(client, "result_summary", result_id=rid)
    assert not err
    assert len(json.dumps(env["data"]).encode()) <= 8 * 1024
    assert "1 error, 1 warning, 1 info" in env["data"]["text"]

    seen: list[dict[str, Any]] = []
    offset: int | None = 0
    while offset is not None:
        env, err = await call(
            client, "list_violations", result_id=rid, page={"offset": offset, "limit": 2}
        )
        assert not err
        seen.extend(env["data"]["items"])
        offset = env["data"]["next_offset"]
    assert len(seen) == env["data"]["total"] == 3
    assert sorted(v["severity"] for v in seen) == ["error", "info", "warning"]
    assert len({v["fingerprint"] for v in seen}) == 3


async def test_text_digest_accompanies_structured_content(client: ClientSession) -> None:
    res = await client.call_tool("open_board", {"path": str(BOARD_FILE)})
    assert res.content[0].text  # type: ignore[union-attr]
    assert res.structuredContent is not None


async def test_misspelled_field_is_an_error_envelope_with_pointer_and_hint(
    client: ClientSession,
) -> None:
    env, err = await call(client, "run_erc", project_id="p_x", waitt_s=3)
    assert err and env["ok"] is False and env["data"] is None
    e = env["errors"][0]
    assert e["code"] == "SETUP_INVALID" and e["path"] == "/waitt_s" and e["hint"]


async def test_unknown_net_gives_not_found_with_candidates(client: ClientSession) -> None:
    env, _ = await call(client, "open_board", path=str(BOARD_FILE))
    env, err = await call(client, "get_net", project_id=env["data"]["project_id"], net="SGI")
    assert err and env["errors"][0]["code"] == "NOT_FOUND"
    assert "SIG" in env["errors"][0]["details"]["candidates"]


async def test_path_outside_roots_is_permission_denied(
    client: ClientSession, tmp_path: Path
) -> None:
    other = tmp_path / "x.oab.json"
    other.write_text(BOARD_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    env, err = await call(client, "open_board", path=str(other))
    assert err and env["errors"][0]["code"] == "PERMISSION_DENIED"


async def test_unknown_tool_is_an_error(client: ClientSession) -> None:
    assert (await client.call_tool("no_such_tool", {})).isError


async def test_dry_run_starts_no_job(client: ClientSession, service: FakeService) -> None:
    env, _ = await call(client, "open_board", path=str(BOARD_FILE))
    pid = env["data"]["project_id"]
    env, err = await call(
        client, "run_analysis", project_id=pid, analysis="dummy.work", config={}, dry_run=True
    )
    assert not err and env["data"]["dry_run"] is True and env["data"]["job"] is None
    env, _ = await call(client, "list_jobs", project_id=pid)
    assert env["data"]["total"] == 0
