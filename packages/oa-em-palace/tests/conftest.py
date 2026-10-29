# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Make ``palace_helpers`` importable under ``--import-mode=importlib``, and let the
``external("palace")`` marker see a Palace inside WSL.

``oa_testkit.plugin.tool_available`` only looks for ``palace`` on the host ``PATH``, so on Windows
it always skips. Here the skip is lifted when the adapter's own discovery finds an install (see
docs/change-requests/WP-301-palace-findings.md, section 5).
"""

import sys
from pathlib import Path

import pytest

_here = str(Path(__file__).parent)
if _here not in sys.path:
    sys.path.insert(0, _here)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    mine = [i for i in items if "oa-em-palace" in str(i.path).replace("\\", "/")]
    if not any(i.get_closest_marker("external") for i in mine):
        return
    from oa_em_palace import discover

    if discover() is None:
        return
    for item in mine:
        item.own_markers = [
            m
            for m in item.own_markers
            if not (m.name == "skip" and "'palace'" in str(m.kwargs.get("reason", "")))
        ]
