# SPDX-License-Identifier: GPL-3.0-or-later
"""Canonical encodings used for content hashes and cache keys."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from pathlib import PurePath
from typing import Any

import numpy as np

from oa_model.arrays import Floats, NDArray, Points


def _sig6(x: float) -> float:
    return float(f"{x:.6g}")


def _nm(x: float) -> int:
    return math.floor(x * 1e9 + 0.5)


def canon(
    obj: Any,
    *,
    nm: bool = False,
    sig_keys: frozenset[str] = frozenset(),
    _sig: bool = False,
) -> Any:
    """Turn a `model_dump()` (python mode) tree into a JSON-able canonical tree.

    nm=False: floats stay exact (repr round-trip) and arrays become {"$sha256": digest}.
    nm=True: floats outside `sig_keys` subtrees become integers in nanometres; inside them they
    are rounded to 6 significant digits.
    """
    if isinstance(obj, Points | Floats | NDArray):
        if nm and isinstance(obj, Points | Floats):
            if _sig:
                return [_sig6(float(v)) for v in obj.array.reshape(-1)]
            ints = np.floor(obj.array * 1e9 + 0.5).astype("<i8")
            return {"$nm": hashlib.sha256(ints.tobytes()).hexdigest()}
        return {"$sha256": obj.digest().hex()}
    if isinstance(obj, dict):
        return {
            str(k): canon(v, nm=nm, sig_keys=sig_keys, _sig=_sig or k in sig_keys)
            for k, v in obj.items()
        }
    if isinstance(obj, frozenset | set):
        items = [canon(v, nm=nm, sig_keys=sig_keys, _sig=_sig) for v in obj]
        return sorted(items, key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(obj, tuple | list):
        return [canon(v, nm=nm, sig_keys=sig_keys, _sig=_sig) for v in obj]
    if isinstance(obj, bool) or obj is None or isinstance(obj, int | str):
        return obj
    if isinstance(obj, float):
        if not nm:
            return obj + 0.0  # -0.0 -> 0.0
        return _sig6(obj) + 0.0 if _sig else _nm(obj)
    if isinstance(obj, PurePath):
        return obj.as_posix()
    raise TypeError(f"cannot canonicalize {type(obj).__name__}")


def sha256_json(tree: Any) -> str:
    """SHA-256 hex of the canonical JSON text of `tree` (sorted keys, compact, no NaN)."""
    text = json.dumps(
        tree, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def join_hashes(parts: Iterable[str]) -> str:
    return sha256_json(list(parts))
