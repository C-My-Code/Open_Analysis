# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Agent-interface tests: the REAL service behind MCP, under a loopback-only socket guard."""

from __future__ import annotations

import ipaddress
import shutil
import socket
import sys
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
BOARDS = REPO / "tests" / "boards" / "kicad10"
_here = str(Path(__file__).resolve().parent)
if _here not in sys.path:
    sys.path.insert(0, _here)

from mcp.client.session import ClientSession  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

from oa_mcp.config import Settings  # noqa: E402
from oa_mcp.server import build_server  # noqa: E402
from oa_service import Registry  # noqa: E402
from oa_service.local import LocalService  # noqa: E402

_REAL_CONNECT = socket.socket.connect


def _is_loopback(address: Any) -> bool:
    if not isinstance(address, tuple) or not address:
        return True  # AF_UNIX and friends
    host = str(address[0])
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def socket_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """pytest-socket stand-in: only loopback connections are allowed in tests/agents."""

    def guarded(self: socket.socket, address: Any) -> None:
        if not _is_loopback(address):
            raise RuntimeError(f"network access blocked by tests/agents socket guard: {address!r}")
        _REAL_CONNECT(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    return tmp_path / "cache"


@pytest.fixture
def boards(tmp_path: Path) -> Path:
    """A writable copy of the synthetic boards the agent tests use."""
    out = tmp_path / "boards"
    for name in ("microstrip", "power_plane", "combined"):
        shutil.copytree(
            BOARDS / name,
            out / name,
            ignore=shutil.ignore_patterns("*.png", "*.zip", "*.xml", "*.json", "*.license"),
        )
    return out


@pytest.fixture
def real_registry(cache_dir: Path) -> Registry:
    return Registry.load(LocalService(cache_dir=cache_dir))


@pytest.fixture
async def mcp_client(real_registry: Registry, boards: Path) -> AsyncIterator[ClientSession]:
    settings = replace(Settings(roots=(str(boards),)), executor="inline", allow_writes=False)
    async with create_connected_server_and_client_session(
        build_server(real_registry, settings)
    ) as session:
        yield session


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
