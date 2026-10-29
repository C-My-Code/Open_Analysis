# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only by default, propose then apply, and budgets, with the real service on `combined`."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session

from oa_mcp.config import Settings
from oa_mcp.server import build_server
from oa_service import Registry

pytestmark = pytest.mark.anyio


def tree_hash(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".oa" not in p.relative_to(root).parts
    }


async def call(c: ClientSession, tool: str, **args: Any) -> tuple[dict[str, Any], bool]:
    res = await c.call_tool(tool, args)
    assert res.structuredContent is not None
    return res.structuredContent, bool(res.isError)


async def test_non_write_tools_leave_user_files_untouched(
    mcp_client: ClientSession, boards: Path
) -> None:
    root = boards / "combined"
    before = tree_hash(root)
    env, err = await call(mcp_client, "open_board", path=str(root / "board.kicad_pcb"))
    assert not err
    pid = env["data"]["project_id"]
    for tool in ("board_summary", "list_nets", "stackup", "list_jobs", "list_setups"):
        _, err = await call(mcp_client, tool, project_id=pid)
        assert not err, tool
    env, err = await call(mcp_client, "run_erc", project_id=pid, wait_s=120)
    assert not err
    rid = env["data"]["job"]["result_id"]
    for tool in ("result_summary", "list_violations", "get_result"):
        _, err = await call(mcp_client, tool, result_id=rid)
        assert not err, tool
    env, err = await call(
        mcp_client, "propose_rule_changes", project_id=pid, from_violations={"result_id": rid}
    )
    assert not err  # a dry run: still no user file changes
    assert tree_hash(root) == before


async def test_propose_apply_flow_on_real_violations(real_registry: Registry, boards: Path) -> None:
    root = boards / "combined"
    settings = replace(Settings(roots=(str(boards),)), executor="inline", allow_writes=True)
    server = build_server(real_registry, settings)
    async with create_connected_server_and_client_session(server) as c:
        env, _ = await call(c, "open_board", path=str(root / "board.kicad_pcb"))
        pid = env["data"]["project_id"]
        env, _ = await call(c, "run_erc", project_id=pid, wait_s=120)
        rid = env["data"]["job"]["result_id"]
        env, err = await call(
            c, "propose_rule_changes", project_id=pid, from_violations={"result_id": rid}
        )
        assert not err, env
        prop = env["data"]
        before = tree_hash(root)
        if not prop["files"]:
            pytest.skip("this ERC build proposes no rule-file changes for the board")
        assert all("unified_diff" in f for f in prop["files"])
        # explicit failure modes first
        _, err = await call(c, "apply_proposal", proposal_id=prop["proposal_id"], confirm_token="x")
        assert err and tree_hash(root) == before
        # then the real apply, once
        args = {"proposal_id": prop["proposal_id"], "confirm_token": prop["confirm_token"]}
        env, err = await call(c, "apply_proposal", **args)
        assert not err, env
        applied = env["data"]["applied"]
        assert all(Path(a["file"]).is_file() for a in applied)
        assert any(a["backup"] for a in applied) or all(a["sha256_before"] is None for a in applied)
        _, err = await call(c, "apply_proposal", **args)
        assert err  # single use
        env, _ = await call(
            c, "propose_rule_changes", project_id=pid, from_violations={"result_id": rid}
        )
        stale, _ = await call(
            c,
            "apply_proposal",
            proposal_id=prop["proposal_id"],
            confirm_token=prop["confirm_token"],
        )
        assert stale["ok"] is False


async def test_apply_without_allow_writes_is_denied(
    mcp_client: ClientSession, boards: Path
) -> None:
    root = boards / "combined"
    env, _ = await call(mcp_client, "open_board", path=str(root / "board.kicad_pcb"))
    pid = env["data"]["project_id"]
    env, err = await call(
        mcp_client,
        "propose_rule_changes",
        project_id=pid,
        changes=[
            {
                "kind": "custom_rule",
                "name": "oa_demo",
                "condition": "A.NetClass == 'HS'",
                "constraint": "clearance",
                "min": "0.2mm",
            }
        ],
    )
    assert not err
    prop = env["data"]
    before = tree_hash(root)
    env, err = await call(
        mcp_client,
        "apply_proposal",
        proposal_id=prop["proposal_id"],
        confirm_token=prop["confirm_token"],
    )
    assert err and env["errors"][0]["code"] == "PERMISSION_DENIED"
    assert tree_hash(root) == before


async def test_budget_dry_run_and_clamp(mcp_client: ClientSession, boards: Path) -> None:
    env, _ = await call(
        mcp_client, "open_board", path=str(boards / "microstrip" / "board.kicad_pcb")
    )
    pid = env["data"]["project_id"]
    env, err = await call(
        mcp_client,
        "run_erc",
        project_id=pid,
        dry_run=True,
        budget={"max_runtime_s": 10**9, "max_memory_gb": 10**6},
    )
    assert not err and env["data"]["job"] is None
    codes = [w["code"] for w in env["warnings"]]
    assert "BUDGET_CLAMPED" in codes  # above the server ceiling: clamped with a warning
    jobs, _ = await call(mcp_client, "list_jobs", project_id=pid)
    assert jobs["data"]["total"] == 0
