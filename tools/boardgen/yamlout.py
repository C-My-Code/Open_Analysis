# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal deterministic YAML 1.1/1.2 emitter (stdlib only) for ``expected.yaml``.

Floats always carry a ``.`` so PyYAML parses ``1.0e-05`` as a float; strings are JSON-quoted unless trivially safe.
"""

from __future__ import annotations

import json
import re

_SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_./-]*$")
_RESERVED = {"true", "false", "null", "yes", "no", "on", "off", "y", "n", "~"}


def _float(v: float) -> str:
    s = repr(float(v))
    if "e" in s or "E" in s:
        m, e = s.lower().split("e")
        if "." not in m:
            m += ".0"
        return f"{m}e{int(e):+03d}".replace("e+", "e+")
    if "." not in s:
        s += ".0"
    return s


def scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return _float(v)
    s = str(v)
    if _SAFE.match(s) and s.lower() not in _RESERVED:
        return s
    return json.dumps(s, ensure_ascii=False)


def _is_scalar(v) -> bool:
    return not isinstance(v, (dict, list))


def _flow(v) -> str | None:
    """Single-line flow form for short scalar lists."""
    if isinstance(v, list) and all(_is_scalar(x) for x in v):
        s = "[" + ", ".join(scalar(x) for x in v) + "]"
        return s if len(s) <= 110 else None
    return None


def _emit(v, ind: int, out: list[str]) -> None:
    pad = "  " * ind
    if isinstance(v, dict):
        for k, x in v.items():
            key = scalar(k)
            if _is_scalar(x):
                out.append(f"{pad}{key}: {scalar(x)}")
            elif isinstance(x, dict) and not x:
                out.append(f"{pad}{key}: {{}}")
            elif isinstance(x, list) and not x:
                out.append(f"{pad}{key}: []")
            elif _flow(x):
                out.append(f"{pad}{key}: {_flow(x)}")
            else:
                out.append(f"{pad}{key}:")
                _emit(x, ind + 1, out)
    else:
        for x in v:
            if _is_scalar(x):
                out.append(f"{pad}- {scalar(x)}")
            elif _flow(x):
                out.append(f"{pad}- {_flow(x)}")
            elif isinstance(x, dict) and x:
                sub: list[str] = []
                _emit(x, ind + 1, sub)
                sub[0] = f"{pad}- " + sub[0].lstrip()
                out.extend(sub)
            else:
                out.append(f"{pad}-")
                _emit(x, ind + 1, out)


def dump(obj) -> str:
    out: list[str] = []
    _emit(obj, 0, out)
    return "\n".join(out) + "\n"
