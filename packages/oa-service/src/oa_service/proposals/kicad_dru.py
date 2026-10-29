# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""A byte-preserving ``.kicad_dru`` reader/writer.

Rules are located by scanning parentheses (quoted strings and ``#`` comments respected). Edits
splice text, so every untouched rule keeps its exact bytes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

HEADER = "(version 1)\n"
_NAME = re.compile(r'\(rule\s+(?:"((?:[^"\\]|\\.)*)"|(\S+?))[\s)]')


@dataclass(frozen=True)
class RuleBlock:
    """One top-level ``(rule ...)`` form: ``text[start:end]`` and its name."""

    name: str
    start: int
    end: int


def _scan(text: str) -> list[tuple[int, int]]:
    """Spans of the top-level parenthesised forms."""
    spans: list[tuple[int, int]] = []
    depth = 0
    start = 0
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c == "#":
            while i < n and text[i] != "\n":
                i += 1
        elif c == "(":
            if depth == 0:
                start = i
            depth += 1
        elif c == ")" and depth > 0:
            depth -= 1
            if depth == 0:
                spans.append((start, i + 1))
        i += 1
    return spans


def parse_rules(text: str) -> list[RuleBlock]:
    """All ``(rule ...)`` blocks in file order."""
    out: list[RuleBlock] = []
    for s, e in _scan(text):
        m = _NAME.match(text, s)
        if m:
            out.append(RuleBlock(m.group(1) if m.group(1) is not None else m.group(2), s, e))
    return out


def has_rule(text: str, name: str) -> bool:
    return any(b.name == name for b in parse_rules(text))


def append_rule(text: str, rule: str) -> str:
    """Add ``rule`` at the end (creating the ``(version 1)`` header for an empty file)."""
    body = text if text.strip() else HEADER
    if not body.endswith("\n"):
        body += "\n"
    return body + rule if rule.endswith("\n") else body + rule + "\n"


def remove_rule(text: str, name: str) -> tuple[str, bool]:
    """Remove the rule called ``name`` (and its line break). Returns ``(text, removed)``."""
    for b in parse_rules(text):
        if b.name == name:
            end = b.end
            if text[end : end + 1] == "\n":
                end += 1
            return text[: b.start] + text[end:], True
    return text, False


def _q(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_rule(
    name: str,
    condition: str,
    constraint: str,
    *,
    min: str | None = None,
    opt: str | None = None,
    max: str | None = None,
    layer: str | None = None,
    severity: str | None = None,
) -> str:
    """KiCad rule text: ``(rule "n" (layer ..) (condition "..") (constraint c (min ..)))``."""
    opts = " ".join(f"({k} {v})" for k, v in (("min", min), ("opt", opt), ("max", max)) if v)
    lines = [f'(rule "{_q(name)}"']
    if layer:
        lines.append(f'  (layer "{_q(layer)}")')
    lines.append(f'  (condition "{_q(condition)}")')
    tail = f"  (constraint {constraint} {opts})" if opts else f"  (constraint {constraint})"
    if severity:
        lines.append(tail)
        lines.append(f"  (severity {severity}))")
    else:
        lines.append(tail + ")")
    return "\n".join(lines) + "\n"
