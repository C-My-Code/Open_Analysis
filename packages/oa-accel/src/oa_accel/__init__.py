# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""oa_accel: one CuPy backend behind a NumPy/SciPy oracle (ADR-0009).

``import oa_accel`` is lazy: it imports neither NumPy, SciPy nor CuPy. Names resolve on first
access.
"""

from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "TOLERANCES",
    "AccelError",
    "AccelReport",
    "Backend",
    "CpuBackend",
    "CupyBackend",
    "Decision",
    "DevicePolicy",
    "ErrorInfo",
    "MemoryBudget",
    "Problem",
    "Thresholds",
    "capabilities",
    "compare",
    "decide",
    "select",
]

_LAZY = {
    "AccelError": "oa_accel.errors",
    "ErrorInfo": "oa_accel.errors",
    "AccelReport": "oa_accel.report",
    "Backend": "oa_accel.backend",
    "CpuBackend": "oa_accel.backend",
    "CupyBackend": "oa_accel.backend",
    "MemoryBudget": "oa_accel.memory",
    "Decision": "oa_accel.policy",
    "DevicePolicy": "oa_accel.policy",
    "Problem": "oa_accel.policy",
    "Thresholds": "oa_accel.policy",
    "decide": "oa_accel.policy",
    "select": "oa_accel.policy",
    "TOLERANCES": "oa_accel.tolerance",
    "compare": "oa_accel.tolerance",
    "capabilities": "oa_accel.doctor",
}


def __getattr__(name: str) -> Any:
    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError(f"module 'oa_accel' has no attribute {name!r}")
    return getattr(importlib.import_module(mod), name)
