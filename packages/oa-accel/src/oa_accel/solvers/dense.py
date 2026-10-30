# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Batched dense solves: systems of equal shape are stacked and factored in one cuSOLVER call."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

from oa_accel import ops
from oa_accel.backend import Assume, Backend
from oa_accel.errors import AccelError

__all__ = ["batched_dense_solve"]

_STACK_MAX_N = 512  # above this a single getrf already fills the GPU; loop instead
_STACK_BYTES = 256 << 20  # device bytes of one stacked chunk of matrices


def batched_dense_solve(
    be: Backend, systems: Sequence[tuple[Any, Any]], assume: Assume = "gen"
) -> list[Any]:
    """Solve every ``(A, b)``; returns host arrays in input order.

    Systems are grouped by ``(n, rhs shape, dtype)``. Small groups on a GPU backend are stacked
    and solved with one batched call; large ones go one by one through ``Backend.dense_solve``
    (which owns the memory check and the CPU fallback). Any batched failure other than a
    singular matrix falls back to that per-system path.
    """
    out: list[Any] = [None] * len(systems)
    groups: dict[tuple[Any, ...], list[int]] = {}
    for i, (a, b) in enumerate(systems):
        groups.setdefault((a.shape, b.shape, str(a.dtype), str(b.dtype)), []).append(i)
    for key, idx in sorted(groups.items(), key=lambda kv: kv[0][0], reverse=True):
        n = key[0][0]
        itemsize = int(np.dtype(key[2]).itemsize)
        per = max(1, _STACK_BYTES // max(n * n * itemsize, 1))
        for c0 in range(0, len(idx), per):
            chunk = idx[c0 : c0 + per]
            done = _stacked(be, [systems[i] for i in chunk], n) if len(chunk) > 1 else None
            if done is None:
                done = [be.dense_solve(systems[i][0], systems[i][1], assume) for i in chunk]
            for i, x in zip(chunk, done, strict=True):
                out[i] = x
    return out


def _stacked(be: Backend, chunk: list[tuple[Any, Any]], n: int) -> list[Any] | None:
    if be.name == "cpu" or n > _STACK_MAX_N:
        return None
    xp = be.xp
    try:
        a = xp.stack([be.to_device(s[0]) for s in chunk])
        b = xp.stack([be.to_device(s[1]) for s in chunk])
        # b of shape (batch, n) is a stack of vectors: solve as (batch, n, 1)
        x = xp.linalg.solve(a, b[..., None])[..., 0] if b.ndim == 2 else xp.linalg.solve(a, b)
        if not bool(xp.isfinite(x).all()):
            raise ops.singular("dense_solve")
        host = be.to_host(x)
    except AccelError:
        raise
    except Exception:  # driver error, OOM, LinAlgError: per-system path decides
        return None
    return [host[k] for k in range(len(chunk))]
