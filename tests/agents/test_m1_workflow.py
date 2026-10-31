# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ROADMAP M1 agent workflow over MCP with the real service (WP-102 boards), WP-303.

An agent-style client does, with no skipped step (every M1 analysis must be installed):
``open_board`` (combined) -> ``impedance_profile`` (diff pairs) -> ``run_erc`` ->
``open_board`` (power_plane) -> ``run_analysis`` (pi.dc) -> ``result_summary`` plus
``list_violations``/``get_result`` for each result.

The flow runs in-process and over a real ``oa-mcp`` stdio subprocess. Every summary must state an
accuracy class, and every result must equal what the CLI end-to-end suite (``tests/e2e``) gets
from the same board file through the real ``oa`` command line.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from pydantic import AnyUrl

_E2E = str(Path(__file__).resolve().parents[1] / "e2e")
if _E2E not in sys.path:
    sys.path.insert(0, _E2E)

from e2e_helpers import (  # noqa: E402
    ACCURACY_CLASSES,
    BOARDS,
    POWER_BOARDS,
    Cli,
    job_of,
    stage_board,
)

pytestmark = pytest.mark.anyio

M1_ANALYSES = {"impedance.profile", "erc", "pi.dc"}
DIFF_PAIRS = {"names": ["USB_D*"]}  # the combined board's USB_D+/USB_D- pair
BOARD_SKIP = ("*.png", "*.zip", "*.xml", "*.json", "*.license")


async def call(c: ClientSession, tool: str, **args: Any) -> dict[str, Any]:
    res = await c.call_tool(tool, args)
    assert res.structuredContent is not None, res
    env: dict[str, Any] = res.structuredContent
    assert env["ok"] is (not res.isError)
    assert env["ok"], f"{tool} failed: {env.get('errors')}"  # every step of the flow must succeed
    return env


async def installed(c: ClientSession) -> set[str]:
    doc = json.loads((await c.read_resource(AnyUrl("oa://analyses"))).contents[0].text)  # type: ignore[union-attr]
    return {a["kind"] for a in doc}


def check_summary(env: dict[str, Any], analysis: str) -> dict[str, Any]:
    """A bounded summary of ``analysis`` whose every item states a known accuracy class."""
    data: dict[str, Any] = env["data"]
    compact = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    assert (
        len(compact.encode()) <= 8 * 1024
    )  # BundleSummary cap (compact JSON, as the service counts)
    assert data["analysis"] == analysis
    assert data["text"] and data["status"] in ("ok", "warning", "failed_quality", "error")
    assert data["items"], data
    for item in data["items"]:
        assert item["accuracy_class"] in ACCURACY_CLASSES, item
    return data


async def get_item(c: ClientSession, result_id: str, item_id: str) -> dict[str, Any]:
    """``get_result`` of one item; it must state its accuracy class."""
    env = await call(c, "get_result", result_id=result_id, item_id=item_id)
    view: dict[str, Any] = env["data"]["item"]
    assert view["item"]["id"] == item_id
    assert view["summary"]["accuracy_class"] in ACCURACY_CLASSES
    return view


def accuracy_of(summary: dict[str, Any]) -> list[str]:
    return sorted({i["accuracy_class"] for i in summary["items"]})


@dataclass
class McpRun:
    """What the agent saw, for the comparison with the CLI."""

    impedance: dict[str, Any]
    erc: dict[str, Any]
    erc_total: int
    dc: dict[str, Any]
    first_open: dict[str, Any]


async def agent_flow(c: ClientSession, boards: Path) -> McpRun:
    """The WP-303 acceptance flow. Nothing may be skipped."""
    assert await installed(c) >= M1_ANALYSES

    # 1. open the 8-layer combined board; nothing is written next to it
    before = sorted(p.name for p in (boards / "combined").iterdir())
    opened = await call(c, "open_board", path=str(boards / "combined" / "board.kicad_pcb"))
    assert opened["data"]["ephemeral"] is True
    pid = opened["data"]["project_id"]
    assert sorted(p.name for p in (boards / "combined").iterdir()) == before
    assert (await call(c, "board_summary", project_id=pid))["data"]["layer_count"] == 8

    # 2. impedance profile of the differential pair
    env = await call(
        c, "impedance_profile", project_id=pid, nets=DIFF_PAIRS, method="auto", wait_s=300
    )
    job = env["data"]["job"]
    assert job["state"] == "succeeded", env
    imp_rid = job["result_id"]
    imp = check_summary(await call(c, "result_summary", result_id=imp_rid), "impedance.profile")
    manifest = (await call(c, "get_result", result_id=imp_rid))["data"]["manifest"]
    assert manifest["analysis"] == "impedance.profile" and manifest["items"], manifest
    first = imp["items"][0]["item_id"]
    await get_item(c, imp_rid, first)

    # 3. ERC, then the violations page by page consistent with the summary
    env = await call(c, "run_erc", project_id=pid, wait_s=300)
    job = env["data"]["job"]
    assert job["state"] == "succeeded", env
    rid = job["result_id"]
    erc = check_summary(await call(c, "result_summary", result_id=rid), "erc")
    seen, total, offset = 0, 0, 0
    while offset is not None:
        page = await call(c, "list_violations", result_id=rid, page={"offset": offset, "limit": 5})
        seen += len(page["data"]["items"])
        total = page["data"]["total"]
        offset = page["data"]["next_offset"]
    assert seen == total
    await get_item(c, rid, "violations")

    # 4. the power board and its DC IR drop (a dry run first)
    opened = await call(c, "open_board", path=str(boards / "power_plane" / "board.kicad_pcb"))
    plane = opened["data"]["project_id"]
    cfg = POWER_BOARDS["power_plane"]
    dry = await call(
        c, "run_analysis", project_id=plane, analysis="pi.dc", config=cfg, dry_run=True
    )
    assert dry["data"]["valid"] is True and dry["data"]["job"] is None
    assert dry["data"]["estimate"]["within_budget"] is True
    env = await call(c, "run_analysis", project_id=plane, analysis="pi.dc", config=cfg, wait_s=300)
    job = env["data"]["job"]
    assert job["state"] == "succeeded", env
    dc_rid = job["result_id"]
    dc = check_summary(await call(c, "result_summary", result_id=dc_rid), "pi.dc")
    # pi.dc has no violations item: the agent gets a NOT_FOUND with a hint, not an empty page
    res = await c.call_tool("list_violations", {"result_id": dc_rid})
    page = res.structuredContent
    assert page is not None and res.isError and page["errors"][0]["code"] == "NOT_FOUND", page
    await get_item(c, dc_rid, dc["items"][0]["item_id"])
    return McpRun(impedance=imp, erc=erc, erc_total=total, dc=dc, first_open=opened["data"])


def cli_reference(boards: Path, tmp: Path) -> McpRun:
    """The same boards through the real ``oa`` CLI (the ``tests/e2e`` path)."""
    out: dict[str, Any] = {}
    for name in ("combined", "power_plane"):
        work = tmp / f"cli_{name}"
        stage_board(boards / name / "board.kicad_pcb", work)
        cli = Cli(work)
        cli.run("init", "--yes", ".")
        out[name] = cli
    comb: Cli = out["combined"]
    imp = comb.run("impedance", "profile", "--nets", json.dumps(DIFF_PAIRS), "--method", "auto")
    erc = comb.run("erc", "--fail-on", "never")
    erc_rid = job_of(erc)["result_id"]
    total = comb.run("results", "violations", erc_rid, "--limit", "1")["data"]["total"]
    plane: Cli = out["power_plane"]
    cfg = plane.cwd / "pi_dc.json"
    cfg.write_text(json.dumps(POWER_BOARDS["power_plane"]), encoding="utf-8")
    dc = plane.run("run", "--analysis", "pi.dc", "--config", f"@{cfg}")

    def summ(cli: Cli, env: dict[str, Any]) -> dict[str, Any]:
        s: dict[str, Any] = cli.run("results", "summary", job_of(env)["result_id"])["data"]
        return s

    return McpRun(
        impedance=summ(comb, imp), erc=summ(comb, erc), erc_total=total, dc=summ(plane, dc),
        first_open={},
    )  # fmt: skip


def assert_same_as_cli(mcp: McpRun, cli: McpRun) -> None:
    """Numbers come from the same code; compare the structure the agent relies on."""
    for key in ("impedance", "erc", "dc"):
        a, b = getattr(mcp, key), getattr(cli, key)
        assert a["text"] == b["text"], key
        assert a["status"] == b["status"], key
        assert accuracy_of(a) == accuracy_of(b), key
        assert [i["item_id"] for i in a["items"]] == [i["item_id"] for i in b["items"]], key
    assert mcp.erc_total == cli.erc_total


@pytest.fixture
def m1_boards(boards: Path) -> Path:
    """The agent-test boards plus the power plane the M1 flow needs."""
    shutil.copytree(
        BOARDS / "power_plane",
        boards / "power_plane",
        ignore=shutil.ignore_patterns(*BOARD_SKIP),
    )
    return boards


async def test_m1_workflow(mcp_client: ClientSession, m1_boards: Path, tmp_path: Path) -> None:
    mcp = await agent_flow(mcp_client, m1_boards)
    assert_same_as_cli(mcp, cli_reference(m1_boards, tmp_path))


async def test_m1_workflow_over_stdio(m1_boards: Path, tmp_path: Path) -> None:
    """The same flow against a real ``oa-mcp`` stdio subprocess (what an agent host starts)."""
    from mcp.client.session import ClientSession as Session

    env = {**os.environ, "OA_DEVICE": "cpu"}
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "oa_mcp", "--root", str(m1_boards), "--executor", "inline"],
        env=env,
    )
    # WP-518: run_analysis(pi.dc) used to hang here on Windows (gmsh's DLL load blocked on the
    # stdin pipe); a hard timeout turns any regression into a failure instead of a stuck run.
    with anyio.fail_after(600):
        async with stdio_client(params) as (read, write), Session(read, write) as session:
            await session.initialize()
            mcp = await agent_flow(session, m1_boards)
    assert_same_as_cli(mcp, cli_reference(m1_boards, tmp_path))
