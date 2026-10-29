# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Agent docs consistency, settings and import hygiene."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from mcp_helpers import REPO_ROOT

from oa_mcp.cli import build_parser
from oa_mcp.config import Settings, parse_duration, parse_memory_gb, resolve_settings
from oa_mcp.contract import TOOLS
from oa_mcp.resources import BUNDLED_DOCS, read_bundled

SOURCES = {
    "agent-guide.md": "docs/agents/AGENTS.md",
    "accuracy.md": "docs/agents/accuracy.md",
    "llms.txt": "llms.txt",
}


def test_bundled_docs_match_sources() -> None:
    for name, src in SOURCES.items():
        assert read_bundled(name) == (REPO_ROOT / src).read_text(encoding="utf-8"), (
            f"{name} is stale; run python tools/agents/sync_docs.py"
        )
    assert {f for f, _ in BUNDLED_DOCS.values()} == set(SOURCES)


def test_llms_txt_links_resolve_and_tools_exist() -> None:
    text = (REPO_ROOT / "llms.txt").read_text(encoding="utf-8")
    assert text.startswith("# Open_Analysis\n")
    for target in re.findall(r"\]\(([^)]+)\)", text):
        assert (REPO_ROOT / target.split("#")[0]).exists(), target
    listed = re.search(r"^- Tools: (.+)\.$", text, re.MULTILINE)
    assert listed
    assert set(listed.group(1).split(", ")) == set(TOOLS)


def test_agent_guide_tool_names_exist() -> None:
    text = (REPO_ROOT / "docs" / "agents" / "AGENTS.md").read_text(encoding="utf-8")
    called = set(re.findall(r"`([a-z_]+)\(", text))
    assert called, "guide should show tool calls"
    assert called <= set(TOOLS), called - set(TOOLS)
    assert {"open_board", "run_erc", "propose_rule_changes", "apply_proposal"} <= called


def test_markdown_links_in_agent_docs_resolve() -> None:
    for rel in ("docs/agents/AGENTS.md", "docs/agents/connect.md", "CLAUDE.md", "AGENTS.md"):
        path = REPO_ROOT / rel
        for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", path.read_text(encoding="utf-8")):
            if "://" not in target:
                assert (path.parent / target).exists(), f"{rel}: {target}"


def test_claude_md_is_short() -> None:
    assert len((REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8").splitlines()) <= 200


def test_parse_units() -> None:
    assert parse_duration("15m") == 900.0
    assert parse_duration("4h") == 14400.0
    assert parse_duration("90") == 90.0
    assert parse_memory_gb("16GB") == 16.0
    assert parse_memory_gb("512MB") == 0.5
    with pytest.raises(ValueError, match="duration"):
        parse_duration("soon")


def test_flags_win_over_config_and_budget_is_clamped(tmp_path: Path) -> None:
    s = resolve_settings(
        {"root": [str(tmp_path)], "max_runtime": "5m", "allow_writes": None},
        {"max_runtime": "1h", "max_memory": "4GB", "allow_writes": True},
    )
    assert s.max_runtime_s == 300.0
    assert s.max_memory_gb == 4.0
    assert s.allow_writes is True
    ctx = s.call_context()
    assert ctx.budget_ceiling.max_runtime_s == 300.0
    assert ctx.default_budget.max_runtime_s == 300.0  # default 600 s clamped to the ceiling
    assert ctx.default_budget.max_memory_gb == 4.0
    assert ctx.roots == (tmp_path.resolve(),)


def test_defaults_are_read_only() -> None:
    s = Settings()
    assert s.allow_writes is False
    ctx = s.call_context()
    assert ctx.default_budget.max_runtime_s == 600.0
    assert ctx.budget_ceiling.max_runtime_s == 4 * 3600.0
    assert ctx.max_response_bytes == 24 * 1024


def test_cli_parser_has_documented_flags() -> None:
    p = build_parser()
    args = p.parse_args(["--http", "--port", "0", "--root", "a", "--root", "b", "--allow-writes"])
    assert args.http and args.root == ["a", "b"] and args.allow_writes


def test_importing_oa_mcp_loads_no_llm_sdk_or_requests() -> None:
    code = (
        "import sys, oa_mcp.cli, oa_mcp.server;"
        "bad=[m for m in ('anthropic','openai','requests') if m in sys.modules];"
        "print(bad)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == "[]"
