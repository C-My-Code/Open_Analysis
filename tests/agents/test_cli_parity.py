# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Parity: an MCP tool's `data` equals the `data` of `oa ... --json` (volatile fields masked)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp.client.session import ClientSession

from oa_service import mask_volatile

pytestmark = pytest.mark.anyio


def oa(cache_dir: Path, *args: str) -> dict[str, Any]:
    env = {**os.environ, "OA_CACHE_DIR": str(cache_dir), "PYTHONIOENCODING": "utf-8"}
    env.pop("OA_OUTPUT", None)
    cp = subprocess.run(
        [sys.executable, "-m", "oa_cli.main", *args, "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )
    lines = cp.stdout.splitlines()
    assert len(lines) == 1, (cp.stdout, cp.stderr)
    doc: dict[str, Any] = json.loads(lines[0])
    return doc


def masked(data: Any) -> Any:
    return mask_volatile(json.loads(json.dumps(data)))


async def tool(c: ClientSession, name: str, **args: Any) -> dict[str, Any]:
    res = await c.call_tool(name, args)
    assert res.structuredContent is not None and not res.isError, res.structuredContent
    data: dict[str, Any] = res.structuredContent["data"]
    return data


async def test_read_tools_match_the_cli(
    mcp_client: ClientSession, boards: Path, cache_dir: Path
) -> None:
    opened = await tool(
        mcp_client, "open_board", path=str(boards / "microstrip" / "board.kicad_pcb")
    )
    pid = opened["project_id"]

    summary = await tool(mcp_client, "board_summary", project_id=pid)
    assert masked(summary) == masked(oa(cache_dir, "board", "summary", pid)["data"])

    nets = await tool(mcp_client, "list_nets", project_id=pid)
    assert masked(nets) == masked(oa(cache_dir, "board", "nets", pid)["data"])

    stack = await tool(mcp_client, "stackup", project_id=pid)
    assert masked(stack) == masked(oa(cache_dir, "board", "stackup", pid)["data"])


async def test_run_result_matches_the_cli(
    mcp_client: ClientSession, boards: Path, cache_dir: Path
) -> None:
    opened = await tool(
        mcp_client, "open_board", path=str(boards / "microstrip" / "board.kicad_pcb")
    )
    pid = opened["project_id"]
    run = await tool(mcp_client, "run_erc", project_id=pid, wait_s=60)
    rid = run["job"]["result_id"]
    via_mcp = await tool(mcp_client, "result_summary", result_id=rid)
    assert masked(via_mcp) == masked(oa(cache_dir, "results", "summary", rid)["data"])
    page = await tool(mcp_client, "list_violations", result_id=rid)
    assert masked(page) == masked(oa(cache_dir, "results", "violations", rid)["data"])
