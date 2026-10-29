# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ROADMAP M1 agent workflow over MCP with the real service (WP-102 boards).

open_board -> impedance_profile -> run_erc -> run_analysis pi.dc -> result_summary. A step is
skipped when its analysis is not installed (WP-303 makes them mandatory).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp.client.session import ClientSession
from pydantic import AnyUrl

pytestmark = pytest.mark.anyio

PLANE_CFG = {
    "rail": {"net": "+1V0", "return": "GND"},
    "vrm": {"component": "U5", "vset": 1.0},
    "loads": [{"component": "U1", "current": "10A"}],
    "temperature": "20degC",
}


async def call(c: ClientSession, tool: str, **args: Any) -> dict[str, Any]:
    res = await c.call_tool(tool, args)
    assert res.structuredContent is not None, res
    env: dict[str, Any] = res.structuredContent
    assert env["ok"] is (not res.isError)
    return env


async def installed(c: ClientSession) -> set[str]:
    doc = json.loads((await c.read_resource(AnyUrl("oa://analyses"))).contents[0].text)  # type: ignore[union-attr]
    return {a["kind"] for a in doc}


def check_summary(env: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = env["data"]
    compact = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    assert (
        len(compact.encode()) <= 8 * 1024
    )  # BundleSummary cap (compact JSON, as the service counts)
    assert data["text"] and data["status"] in ("ok", "warning", "failed_quality", "error")
    return data


async def test_m1_workflow(mcp_client: ClientSession, boards: Path) -> None:
    c = mcp_client
    kinds = await installed(c)

    # 1. open the 4-layer microstrip board; nothing is written next to it
    before = sorted(p.name for p in (boards / "microstrip").iterdir())
    env = await call(c, "open_board", path=str(boards / "microstrip" / "board.kicad_pcb"))
    assert env["data"]["ephemeral"] is True
    pid = env["data"]["project_id"]
    assert sorted(p.name for p in (boards / "microstrip").iterdir()) == before
    env = await call(c, "board_summary", project_id=pid)
    assert env["data"]["layer_count"] == 4

    # 2. impedance profile (closed form keeps it fast)
    if "impedance.profile" in kinds:
        env = await call(
            c,
            "impedance_profile",
            project_id=pid,
            nets={"kinds": ["signal"]},
            method="closed_form",
            wait_s=120,
        )
        assert env["data"]["job"]["state"] == "succeeded"
        check_summary(await call(c, "result_summary", result_id=env["data"]["job"]["result_id"]))

    # 3. ERC, then read violations page by page consistent with the summary
    if "erc" in kinds:
        env = await call(c, "run_erc", project_id=pid, wait_s=120)
        job = env["data"]["job"]
        assert job["state"] == "succeeded"
        rid = job["result_id"]
        summary = check_summary(await call(c, "result_summary", result_id=rid))
        assert summary["analysis"] == "erc"
        total = None
        seen = 0
        offset: int | None = 0
        while offset is not None:
            page = await call(
                c, "list_violations", result_id=rid, page={"offset": offset, "limit": 5}
            )
            seen += len(page["data"]["items"])
            total = page["data"]["total"]
            offset = page["data"]["next_offset"]
        assert seen == total

    # 4. DC IR drop on the power plane board
    if "pi.dc" in kinds:
        env = await call(c, "open_board", path=str(boards / "power_plane" / "board.kicad_pcb"))
        plane = env["data"]["project_id"]
        dry = await call(
            c, "run_analysis", project_id=plane, analysis="pi.dc", config=PLANE_CFG, dry_run=True
        )
        assert dry["data"]["valid"] is True and dry["data"]["job"] is None
        assert dry["data"]["estimate"]["within_budget"] is True
        env = await call(
            c,
            "run_analysis",
            project_id=plane,
            analysis="pi.dc",
            config=PLANE_CFG,
            wait_s=300,
        )
        job = env["data"]["job"]
        assert job["state"] == "succeeded", env
        summary = check_summary(await call(c, "result_summary", result_id=job["result_id"]))
        assert summary["analysis"] == "pi.dc"
        assert "drop" in summary["text"].lower()

    if not kinds & {"impedance.profile", "erc", "pi.dc"}:
        pytest.skip("no analysis installed")
