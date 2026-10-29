# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""GPU memory budget and up-front estimates (ADR-0009 section 4)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from oa_accel.errors import AccelError, ErrorInfo

GIB = 1 << 30
DEFAULT_PINNED_GB = 2.0
RESERVE_GB = 2.0  # left to the display / driver on the default budget


def _env_float(env: Mapping[str, str], key: str) -> float | None:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return None
    try:
        val = float(raw)
    except ValueError:
        val = -1.0
    if val < 0:
        raise AccelError(
            ErrorInfo(
                code="ACCEL_BAD_CONFIG",
                message=f"{key}={raw!r} is not a non-negative number of GiB.",
                path=f"env.{key}",
                hint=f"Set {key} to a number such as 12 (GiB).",
            )
        )
    return val


def default_gpu_bytes(total_bytes: int) -> int:
    """``min(0.75 x total, total - 2 GiB)``, never negative [bytes]."""
    return max(0, int(min(0.75 * total_bytes, total_bytes - RESERVE_GB * GIB)))


@dataclass(frozen=True)
class MemoryBudget:
    gpu_bytes: int  # [bytes] cap on the CuPy device memory pool
    pinned_bytes: int  # [bytes] cap on pinned host staging memory; counts in the host budget
    total_bytes: int = 0  # [bytes] device total, 0 when unknown

    @classmethod
    def from_env(
        cls,
        total_bytes: int,
        *,
        gpu_gb: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> MemoryBudget:
        """``OA_GPU_MEM_GB`` / ``OA_GPU_PINNED_GB`` with the ADR defaults; ``gpu_gb`` wins."""
        e = os.environ if env is None else env
        gpu = gpu_gb if gpu_gb is not None else _env_float(e, "OA_GPU_MEM_GB")
        pinned = _env_float(e, "OA_GPU_PINNED_GB")
        gpu_bytes = default_gpu_bytes(total_bytes) if gpu is None else int(gpu * GIB)
        if total_bytes:
            gpu_bytes = min(gpu_bytes, total_bytes)  # a budget above the device is meaningless
        pinned_gb = DEFAULT_PINNED_GB if pinned is None else pinned
        return cls(gpu_bytes=gpu_bytes, pinned_bytes=int(pinned_gb * GIB), total_bytes=total_bytes)

    def check(self, estimate: int, op: str) -> ErrorInfo | None:
        """``ACCEL_OUT_OF_MEMORY`` info when ``estimate`` exceeds the budget, else None."""
        if estimate <= self.gpu_bytes:
            return None
        return ErrorInfo(
            code="ACCEL_OUT_OF_MEMORY",
            message=f"{op} needs about {estimate / GIB:.2f} GiB on the GPU; the budget is "
            f"{self.gpu_bytes / GIB:.2f} GiB.",
            path=op,
            hint=f"estimate={estimate} B, budget={self.gpu_bytes} B. Raise OA_GPU_MEM_GB, reduce "
            "the problem, or use device cpu.",
        )

    def require(self, estimate: int, op: str) -> None:
        """Refuse before allocating: raises :class:`AccelError` when over budget."""
        info = self.check(estimate, op)
        if info is not None:
            raise AccelError(info)

    def apply(self, cupy: Any) -> None:
        """Set the CuPy device pool limit. Call before the first allocation.

        CuPy's pinned pool has no limit API; ``pinned_bytes`` is recorded for host accounting
        and not enforced by oa_accel yet.
        """
        cupy.get_default_memory_pool().set_limit(size=self.gpu_bytes)


# ---- estimates (bytes). Heuristics checked against the measured pool peak in the GPU tests.


def dense_solve_bytes(n: int, nrhs: int = 1, itemsize: int = 8, assume: str = "gen") -> int:
    """A (device copy), LU/Cholesky factor, B, X and workspace: ~2 n^2 + 3 n nrhs elements."""
    del assume  # LU and Cholesky have the same footprint to first order
    return (2 * n * n + 3 * n * nrhs) * itemsize


def sparse_solve_bytes(
    n: int, nnz: int, nrhs: int = 1, method: str = "direct", itemsize: int = 8, fill: float = 30.0
) -> int:
    """CSR (values + int32 indices), vectors, and for direct the factor (``fill`` x nnz)."""
    csr = nnz * (itemsize + 4) + (n + 1) * 4
    vec = 5 * n * nrhs * itemsize  # b, x, r, p, Ap (CG) or work vectors
    factor = int(fill * nnz * itemsize) if method == "direct" else 0
    return csr + vec + factor


def stencil_bytes(shape: tuple[int, ...], itemsize: int = 8) -> int:
    """Input, output and one temporary."""
    n = 1
    for s in shape:
        n *= int(s)
    return 3 * n * itemsize
