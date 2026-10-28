# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from pathlib import Path

import pytest

from oa_service import CallContext, Registry
from oa_service.testing import FakeService


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
