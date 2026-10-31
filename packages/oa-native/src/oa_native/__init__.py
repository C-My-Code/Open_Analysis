# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional compiled core. ``OA_NATIVE=0|1|auto`` selects it; callers fall back to Python.

``get()`` returns the compiled ``_native`` module, or ``None`` when it is disabled (``0``) or
not installed (``auto``). ``OA_NATIVE=1`` makes a missing module an ``ImportError``.

In ``auto`` mode a missing or stale module (one built from older crate sources, without some
kernel of ``KERNELS``) is logged once as a warning on the ``oa_native`` logger, and
``status()`` reports it (WP-520): the fallbacks are correct but much slower.
"""

from __future__ import annotations

import importlib
import logging
import os
from types import ModuleType
from typing import Any

__all__ = ["KERNELS", "available", "get", "mode", "status"]

KERNELS = (
    "parse",
    "ring_self_intersects",
    "all_points_inside",
    "cut_intervals",
    "rect_cover_edge",
    "bem_influence",
)
"""Functions the crate of this source tree exports; a module without one of them is stale."""

_log = logging.getLogger("oa_native")
_warned: set[str] = set()


def mode() -> str:
    v = os.environ.get("OA_NATIVE", "auto").strip().lower()
    return v if v in ("0", "1", "auto") else "auto"


def _warn_once(key: str, msg: str, *args: object) -> None:
    if key not in _warned:
        _warned.add(key)
        _log.warning(msg, *args)


def get() -> ModuleType | None:
    m = mode()
    if m == "0":
        return None
    try:
        mod = importlib.import_module("oa_native._native")
    except ImportError as exc:
        if m == "1":
            raise
        _warn_once(
            "missing",
            "oa_native: the compiled module is not importable (%s); running the pure-Python "
            "fallbacks, which are much slower on large boards. Build it with "
            "`uv sync --reinstall-package oa-native` (needs a Rust toolchain).",
            exc,
        )
        return None
    missing = _missing(mod)
    if missing:
        _warn_once(
            "stale",
            "oa_native: the compiled module %s is stale (no %s); those kernels run on the "
            "pure-Python fallbacks. Rebuild it with `uv sync --reinstall-package oa-native`.",
            getattr(mod, "__file__", "?"),
            ", ".join(missing),
        )
    return mod


def _missing(mod: ModuleType) -> list[str]:
    return [k for k in KERNELS if not hasattr(mod, k)]


def available() -> bool:
    try:
        return get() is not None
    except ImportError:
        return False


def status() -> dict[str, Any]:
    """JSON-able state of the compiled module (for ``oa doctor``); never raises.

    ``available``: importable; ``stale``: importable but without some kernel of ``KERNELS``
    (``missing``); ``fallback``: some work runs on the pure-Python oracles although the mode
    does not ask for that.
    """
    m = mode()
    out: dict[str, Any] = {"mode": m, "available": False, "stale": False, "missing": []}
    try:
        mod = importlib.import_module("oa_native._native")
    except ImportError as exc:
        out.update(path=None, kernel_version=None, error=str(exc), missing=list(KERNELS))
        out["fallback"] = m != "0"
        return out
    missing = _missing(mod)
    kv = getattr(mod, "kernel_version", None)
    out.update(
        available=True,
        path=getattr(mod, "__file__", None),
        kernel_version=kv() if callable(kv) else None,
        stale=bool(missing),
        missing=missing,
        fallback=m != "0" and bool(missing),
    )
    return out
