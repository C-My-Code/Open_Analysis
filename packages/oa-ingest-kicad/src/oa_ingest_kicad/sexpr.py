# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tolerant S-expression tokenizer and tree builder for KiCad files.

A node is a plain ``list``: ``[head, child, child, ...]``. A child is a ``str`` (atom, quotes
removed), a nested node, or an ``(N, 2)`` float64 ``numpy`` array. The array form is used for
``(pts (xy x y) ...)`` lists made only of ``xy`` points (the bulk of a large board, e.g.
``filled_polygon``); they are parsed straight into numpy and never become Python tuples. A
``pts`` list that contains ``arc`` items falls back to the generic node form.

The tokenizer is a single compiled regular expression, so a file is scanned in one pass without
recursion. Parsing is error tolerant: unbalanced input raises :class:`SExprError` with a
line number, nothing else is fatal.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TypeAlias

import numpy as np
import numpy.typing as npt

Node: TypeAlias = list  # [head, *children]; children are str | Node | ndarray


class SExprError(ValueError):
    """Malformed S-expression."""

    def __init__(self, message: str, line: int | None = None) -> None:
        super().__init__(message if line is None else f"{message} (line {line})")
        self.line = line


_NUM = r"[^\s()]+"
_TOKEN = re.compile(
    rf'\(pts(?:\s*\(xy\s+{_NUM}\s+{_NUM}\s*\))+\s*\)'  # fast path: all-xy point list
    r'|\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+'
)
_XY_STRIP = re.compile(r"\(pts|\(xy|\)")
_UNESCAPE = re.compile(r"\\(.)")


def tokenize(text: str) -> Iterator[str]:
    """Yield raw tokens: ``(``, ``)``, atoms, quoted strings (with quotes) and fast ``pts`` lists."""
    for m in _TOKEN.finditer(text):
        yield m.group()


def _pts_array(tok: str) -> npt.NDArray[np.float64]:
    values = _XY_STRIP.sub(" ", tok).split()
    return np.array(values, dtype=np.float64).reshape(-1, 2)


def parse(text: str) -> Node:
    """Parse ``text`` and return the first top-level node."""
    root: Node = []
    stack: list[Node] = []
    cur = root
    for tok in _TOKEN.findall(text):
        c = tok[0]
        if c == "(":
            if len(tok) == 1:
                n: Node = []
                cur.append(n)
                stack.append(cur)
                cur = n
            else:
                cur.append(["pts", _pts_array(tok)])
        elif c == ")":
            if not stack:
                raise SExprError("unbalanced ')'", _line_of(text, tok))
            cur = stack.pop()
        elif c == '"':
            s = tok[1:-1]
            cur.append(_UNESCAPE.sub(r"\1", s) if "\\" in s else s)
        else:
            cur.append(tok)
    if stack:
        raise SExprError("unexpected end of input: missing ')'")
    if not root or not isinstance(root[0], list):
        raise SExprError("no S-expression found")
    return root[0]


def _line_of(text: str, tok: str) -> int | None:
    # only used on the error path; the position of the first stray ')' is good enough
    depth = 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return text.count("\n", 0, i) + 1
    return None


# ----------------------------------------------------------------------------------------
# navigation helpers
# ----------------------------------------------------------------------------------------
def head(node: Node) -> str:
    h = node[0] if node else ""
    return h if isinstance(h, str) else ""


def child(node: Node, key: str) -> Node | None:
    """First child node with head ``key``."""
    for c in node:
        if isinstance(c, list) and c and c[0] == key:
            return c
    return None


def children(node: Node, key: str) -> list[Node]:
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def atoms(node: Node) -> list[str]:
    """Atom children (after the head), in order."""
    return [c for c in node[1:] if isinstance(c, str)]


def has_atom(node: Node, name: str) -> bool:
    return any(c == name for c in node[1:] if isinstance(c, str))


def get_str(node: Node, key: str, default: str | None = None) -> str | None:
    c = child(node, key)
    if c is None:
        return default
    for v in c[1:]:
        if isinstance(v, str):
            return v
    return default


def get_float(node: Node, key: str, default: float | None = None) -> float | None:
    s = get_str(node, key)
    if s is None:
        return default
    try:
        return float(s)
    except ValueError:
        return default


def get_floats(node: Node, key: str) -> list[float] | None:
    c = child(node, key)
    if c is None:
        return None
    out: list[float] = []
    for v in c[1:]:
        if isinstance(v, str):
            try:
                out.append(float(v))
            except ValueError:
                return None
    return out


def get_bool(node: Node, key: str, default: bool = False) -> bool:
    """``(key yes)``, ``(key no)`` and the bare ``(key)`` form (true)."""
    c = child(node, key)
    if c is None:
        return default
    for v in c[1:]:
        if isinstance(v, str):
            return v.lower() not in ("no", "false", "0")
    return True
