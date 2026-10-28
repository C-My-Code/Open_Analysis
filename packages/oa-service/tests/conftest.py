# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Make the sibling ``service_helpers`` module importable under ``--import-mode=importlib``."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from oa_service import CallContext, Registry
from oa_service.testing import FakeService

_here = str(Path(__file__).parent)
if _here not in sys.path:
    sys.path.insert(0, _here)


@pytest.fixture
def fake(tmp_path: Path) -> FakeService:
    return FakeService(workdir=tmp_path / "fake")


@pytest.fixture
def ctx() -> CallContext:
    return CallContext(caller="test")


@pytest.fixture
def reg(fake: FakeService) -> Registry:
    return Registry(fake)


@pytest.fixture
def pid(fake: FakeService) -> str:
    return str(fake.open_fixture("diff_pair").project_id)
