# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional compiled core. ``OA_NATIVE=0|1|auto`` selects it; callers fall back to Python.

``get()`` returns the compiled ``_native`` module, or ``None`` when it is disabled (``0``) or
not installed (``auto``). ``OA_NATIVE=1`` makes a missing module an ``ImportError``.
"""

from __future__ import annotations

import importlib
import os
from types import ModuleType

__all__ = ["available", "get", "mode"]


def mode() -> str:
    v = os.environ.get("OA_NATIVE", "auto").strip().lower()
    return v if v in ("0", "1", "auto") else "auto"


def get() -> ModuleType | None:
    m = mode()
    if m == "0":
        return None
    try:
        return importlib.import_module("oa_native._native")
    except ImportError:
        if m == "1":
            raise
        return None


def available() -> bool:
    try:
        return get() is not None
    except ImportError:
        return False
