# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Guarded device sections: a whole multi-kernel solve under one memory check and one fallback.

``Backend.dense_solve`` guards a single call and synchronises the whole device after it. A solver
that runs many short kernels per section (the BEM block elimination of WP-519) is guarded here
instead, once, without the device-wide synchronisation: concurrent host threads then keep the
GPU busy with each other's kernels.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from oa_accel.backend import Backend
from oa_accel.errors import AccelError, ErrorInfo

__all__ = ["run_guarded"]

T = TypeVar("T")

# input errors are not device failures: the CPU path would fail the same way
_PASS_THROUGH = frozenset({"ACCEL_SINGULAR", "ACCEL_BAD_INPUT", "ACCEL_NOT_CONVERGED"})


def run_guarded(
    be: Backend,
    op: str,
    estimate: int,
    gpu_fn: Callable[[], T],
    cpu_fn: Callable[[str], T],
) -> T:
    """``gpu_fn()`` on the device; on a device failure (out of memory, driver error, an injected
    fault) ``cpu_fn(reason)`` when the backend's fallback is ``"cpu"``.

    The memory budget is checked before ``gpu_fn`` runs (``estimate`` bytes). Singular systems and
    bad input are errors of the problem, not of the device, and propagate. The fallback is
    recorded in ``be.report().fallbacks``."""
    reason: str
    try:
        fault: Exception | None = getattr(be, "_fault", None)
        if fault is not None:
            raise fault
        be.memory.require(estimate, op)
        return gpu_fn()
    except AccelError as exc:
        if exc.info.code in _PASS_THROUGH:
            raise
        info: ErrorInfo = exc.info
        reason = info.code
        err: Exception = exc
    except Exception as exc:
        if isinstance(exc, ArithmeticError | ValueError) and not _is_device_error(exc):
            raise
        reason = type(exc).__name__
        err = exc
    if be.fallback != "cpu":
        raise err
    be.report().fallbacks.append(f"{op}: {reason}")
    return cpu_fn(reason)


def _is_device_error(exc: Exception) -> bool:
    mod = type(exc).__module__ or ""
    return mod.startswith(("cupy", "cupyx", "cuda"))
