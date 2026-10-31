# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Registers the ``gpu`` marker (root pyproject is not ours) and skips GPU tests cleanly.

GPU tests run only with ``OA_RUN_GPU=1`` (held by whoever has the GPU token, TASKS rule 11) and
only when CuPy sees a device. Otherwise they are skipped, so CI without a GPU stays green.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# make ``accel_helpers`` importable under --import-mode=importlib
_here = str(Path(__file__).parent)
if _here not in sys.path:
    sys.path.insert(0, _here)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "gpu: needs a GPU and OA_RUN_GPU=1")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    gpu_items = [i for i in items if i.get_closest_marker("gpu")]
    if not gpu_items:
        return
    reason = None
    if os.environ.get("OA_RUN_GPU") != "1":
        reason = "GPU tests need OA_RUN_GPU=1"
    else:
        from oa_accel.probe import probe

        pr = probe()
        if not pr.cupy_available:
            reason = f"no CuPy device available ({pr.error})"
    if reason:
        skip = pytest.mark.skip(reason=reason)
        for item in gpu_items:
            item.add_marker(skip)
