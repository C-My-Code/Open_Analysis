# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small shared helpers: ids, time, canonical JSON, hashing, importing factories."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid() -> str:
    """26-character ULID: 48-bit millisecond timestamp plus 80 random bits."""
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    return "".join(_CROCKFORD[(value >> (5 * i)) & 31] for i in range(25, -1, -1))


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, compact separators, no NaN. Key order never matters."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_callable(spec: str) -> Callable[..., Any]:
    """Resolve ``"package.module:attr"``."""
    mod, _, attr = spec.partition(":")
    if not mod or not attr:
        raise ValueError(f"expected 'module:callable', got {spec!r}")
    obj: Any = importlib.import_module(mod)
    for part in attr.split("."):
        obj = getattr(obj, part)
    if not callable(obj):
        raise TypeError(f"{spec!r} is not callable")
    fn: Callable[..., Any] = obj
    return fn


def atomic_write_text(path: os.PathLike[str] | str, text: str) -> None:
    """Write ``text`` to ``path`` through a temp file and ``os.replace``."""
    tmp = f"{path}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)
