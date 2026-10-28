# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The committed schemas/ files match a fresh regeneration (CI diffs the same thing)."""

from __future__ import annotations

from pathlib import Path

from oa_service import Registry
from oa_service.schemas import check_schemas

ROOT = Path(__file__).resolve().parents[3] / "schemas"


def test_committed_schemas_in_sync(reg: Registry) -> None:
    assert ROOT.is_dir()
    assert check_schemas(reg, ROOT) == [], "run: uv run python -m oa_service.schemas schemas"
