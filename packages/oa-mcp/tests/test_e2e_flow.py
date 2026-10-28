# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""End-to-end flow through an in-process MCP client: open, summarize, ERC, read the summary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp.client.session import ClientSession
from mcp_fake_service import FakeOaService
from mcp_helpers import BOARD_DIR

pytestmark = pytest.mark.anyio


async def call(client: ClientSession, tool: str, **args: Any) -> tuple[dict[str, Any], bool]:
    res = await client.call_tool(tool, args)
    assert res.structuredContent is not None
    assert res.content and res.content[0].type == "text"
    return res.structuredContent, bool(res.isError)


async def test_open_summarize_erc_and_read_summary(client: ClientSession) -> None:
    env, err = await call(client, "open_board", path=str(BOARD_DIR))
    assert not err and env["ok"] is True
    pid = env["data"]["project_id"]
    assert env["data"]["ephemeral"] is True

    env, err = await call(client, "board_summary", project_id=pid)
    assert not err
    assert env["data"]["layer_count"] == 4

    env, err = await call(client, "run_erc", project_id=pid, wait_s=60)
    assert not err
    rid = env["data"]["result_id"]

    env, err = await call(client, "result_summary", result_id=rid)
    assert not err
    summary = env["data"]
    assert len(json.dumps(summary).encode()) <= 8 * 1024
    counts = summary["diagnostics"]
    assert counts["error"] == 2
    assert counts["warning"] == 2
    assert counts["info"] == 1

    # list_violations pages agree with the summary counts
    seen: list[dict[str, Any]] = []
    offset: int | None = 0
    while offset is not None:
        env, err = await call(client, "list_violations", result_id=rid, offset=offset, limit=2)
        assert not err
        seen.extend(env["data"]["items"])
        offset = env["data"]["next_offset"]
    assert len(seen) == env["data"]["total"] == sum(counts[k] for k in ("error", "warning", "info"))
    for sev in ("error", "warning", "info"):
        assert sum(v["severity"] == sev for v in seen) == counts[sev]
    assert len({v["fingerprint"] for v in seen}) == len(seen)


async def test_text_digest_accompanies_structured_content(client: ClientSession) -> None:
    res = await client.call_tool("open_board", {"path": str(BOARD_DIR)})
    assert res.content[0].text.startswith("board.kicad_pcb:")  # type: ignore[union-attr]


async def test_open_board_writes_nothing_next_to_the_board(
    client: ClientSession, tmp_path: Path
) -> None:
    before = sorted(p.name for p in BOARD_DIR.iterdir())
    await call(client, "open_board", path=str(BOARD_DIR / "board.kicad_pcb"))
    assert sorted(p.name for p in BOARD_DIR.iterdir()) == before


async def test_misspelled_field_is_an_error_envelope_with_pointer_and_hint(
    client: ClientSession,
) -> None:
    env, err = await call(client, "run_erc", project_id="p_x", waitt_s=3)
    assert err and env["ok"] is False and env["data"] is None
    e = env["errors"][0]
    assert e["code"] == "SETUP_INVALID"
    assert e["path"] == "/waitt_s"
    assert e["hint"]


async def test_unknown_net_gives_not_found_with_candidates(client: ClientSession) -> None:
    env, _ = await call(client, "open_board", path=str(BOARD_DIR))
    pid = env["data"]["project_id"]
    env, err = await call(client, "get_net", project_id=pid, net="SGI")
    assert err
    assert env["errors"][0]["code"] == "NOT_FOUND"
    assert "SIG" in env["errors"][0]["details"]["candidates"]


async def test_path_outside_roots_is_permission_denied(
    client: ClientSession, tmp_path: Path
) -> None:
    env, err = await call(client, "open_board", path=str(tmp_path))
    assert err and env["errors"][0]["code"] == "PERMISSION_DENIED"


async def test_unknown_tool_is_an_error(client: ClientSession) -> None:
    res = await client.call_tool("no_such_tool", {})
    assert res.isError


async def test_progress_notifications_reach_the_client(client: ClientSession) -> None:
    env, _ = await call(client, "open_board", path=str(BOARD_DIR))
    events: list[tuple[float, str | None]] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        events.append((progress, message))

    res = await client.call_tool(
        "run_erc",
        {"project_id": env["data"]["project_id"]},
        progress_callback=on_progress,
    )
    assert not res.isError
    assert events == [(0.5, "halfway")]


async def test_oversize_response_is_replaced_by_truncation_error(
    client: ClientSession, service: FakeOaService
) -> None:
    env, _ = await call(client, "open_board", path=str(BOARD_DIR))
    service.inflate_bytes = 100_000
    env, err = await call(client, "list_nets", project_id=env["data"]["project_id"])
    assert err
    assert env["errors"][0]["code"] == "RESULT_TRUNCATED"
    assert len(json.dumps(env)) < 2_000
