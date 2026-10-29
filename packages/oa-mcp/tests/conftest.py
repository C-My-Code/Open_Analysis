# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fixtures for the oa-mcp tests."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

# Make the sibling helper modules importable under --import-mode=importlib.
_here = str(Path(__file__).resolve().parent)
if _here not in sys.path:
    sys.path.insert(0, _here)

from mcp.client.session import ClientSession  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402
from mcp_helpers import REPO_ROOT  # noqa: E402

from oa_mcp.config import Settings  # noqa: E402
from oa_mcp.server import build_server  # noqa: E402
from oa_service import Registry  # noqa: E402
from oa_service.testing import FakeService  # noqa: E402


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings() -> Settings:
    return Settings(roots=(str(REPO_ROOT / "tests"),))


@pytest.fixture
def service(tmp_path: Path) -> FakeService:
    return FakeService(workdir=tmp_path / "fake")


@pytest.fixture
def registry(service: FakeService) -> Registry:
    return Registry.load(service)


@pytest.fixture
def server(registry: Registry, settings: Settings) -> FastMCP:
    return build_server(registry, settings)


@pytest.fixture
async def client(server: FastMCP) -> AsyncIterator[ClientSession]:
    async with create_connected_server_and_client_session(server) as session:
        yield session
