# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""No network / no LLM SDK in the core, and the agent docs stay consistent."""

from __future__ import annotations

import importlib.util
import socket
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
CORE = [
    "oa_model",
    "oa_results",
    "oa_framework",
    "oa_jobs",
    "oa_service",
    "oa_mcp",
    "oa_cli",
    "oa_server",
    "oa_erc",
    "oa_pi",
    "oa_impedance",
    "oa_ingest_kicad",
    "oa_geometry",
]


def _tool(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / "tools" / "agents" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_socket_guard_blocks_off_host_connections() -> None:
    s = socket.socket()
    try:
        with pytest.raises(RuntimeError, match="socket guard"):
            s.connect(("203.0.113.7", 80))
    finally:
        s.close()


def test_socket_guard_allows_loopback() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.socket()
    try:
        client.connect(server.getsockname())
    finally:
        client.close()
        server.close()


def test_core_imports_load_no_llm_sdk_or_http_client_libraries() -> None:
    code = (
        "import sys\n"
        + "".join(f"import {m}\n" for m in CORE)
        + "bad = [m for m in ('anthropic', 'openai', 'requests', 'aiohttp', 'urllib3')"
        " if m in sys.modules]\nprint(bad)\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == "[]"


def test_llms_full_is_current() -> None:
    assert _tool("build_llms").main(["--check"]) == 0


def test_agent_doc_links_and_tool_names() -> None:
    from oa_mcp.contract import TOOLS

    check = _tool("check_docs")
    assert check.broken_links() == []
    assert check.unknown_tools(TOOLS) == []


def test_every_documented_tool_exists_in_the_real_catalog() -> None:
    from oa_mcp.contract import TOOLS
    from oa_service import Registry
    from oa_service.testing import FakeService

    reg = Registry.load(FakeService())
    exposed = {s.mcp_tool for s in reg.specs() if s.mcp_tool}
    assert set(TOOLS) <= exposed
    assert {c for c, _ in TOOLS.values()} <= {s.name for s in reg.specs()}
    for tool, (command, effect) in TOOLS.items():
        spec = reg.by_mcp_tool(tool)
        assert spec is not None and spec.spec.name == command
        assert spec.spec.side_effect == effect, tool
