# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native S-expression parser against the pure-Python oracle (``sexpr.parse_py``).

Skipped when the compiled module is not installed; everything else in the repo then runs on
Python (WP-508 acceptance).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from oa_ingest_kicad import sexpr

native = pytest.importorskip("oa_native._native")

ROOT = Path(__file__).resolve().parents[3]
WP102 = sorted((ROOT / "tests" / "boards").rglob("*.kicad_pcb"))
DEMOS = Path(os.environ.get("KICAD_DEMOS", r"D:\Program Files\KiCad\10.0\share\kicad\demos"))
# the 88 MB Jetson board is left to the gate run
DEMO_BOARDS = (
    sorted(p for p in DEMOS.rglob("*.kicad_pcb") if p.stat().st_size < 20_000_000)
    if DEMOS.is_dir()
    else []
)


def same(a: Any, b: Any) -> bool:
    """Bit-exact tree equality (arrays compared by dtype, shape and bytes)."""
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        return (
            isinstance(a, np.ndarray)
            and isinstance(b, np.ndarray)
            and a.dtype == b.dtype
            and a.shape == b.shape
            and a.tobytes() == b.tobytes()
        )
    if isinstance(a, list):
        return (
            isinstance(b, list)
            and len(a) == len(b)
            and all(same(x, y) for x, y in zip(a, b, strict=True))
        )
    return type(a) is type(b) and a == b


SNIPPETS = [
    "(a b c)",
    '(a "q s" "esc \\"x\\" \\\\ y" (b 1 2))',
    "(a (pts (xy 1 2) (xy 3.5 -4e-3)) (pts (xy 1 2) (arc 1 2 3)) (pts))",
    "(a (pts\n (xy 1 2)\n  (xy 3 4 )\n ) (ptsx (xy 1 2)))",
    "(a (pts (xy 1 2)(xy 3 4)) (pts (xy 1)) (pts (xy 1 2) junk))",
    '(a "unterminated (b) c',
    '(a "line\\\nbreak" d)',
    '(a \u00e9\u4e2d\u6587 "\u00fc" (b\u00a0c))',
    "(a)(b)",
    "(a (xy 1 2) (xyz 1 2))",
    "(pts (xy 1 2))",
    "(a (pts (xy 1e400 -inf) (xy nan 0x1)))",
]

REJECTED = ["", "atom", "(a", "a)", "(a))", ")(", "(a (pts (xy foo 2)))"]


@pytest.mark.parametrize("text", SNIPPETS)
def test_snippet_equals_oracle(text: str) -> None:
    got = native.parse(text)
    try:
        want = sexpr.parse_py(text)
    except (sexpr.SExprError, ValueError):
        assert got is None
        return
    assert got is not None
    assert same(got, want), text


@pytest.mark.parametrize("text", REJECTED)
def test_rejected_input_defers_to_python(text: str) -> None:
    assert native.parse(text) is None
    with pytest.raises((sexpr.SExprError, ValueError)):
        sexpr.parse_py(text)
    with pytest.raises((sexpr.SExprError, ValueError)):
        sexpr.parse(text)


@pytest.mark.parametrize("path", WP102, ids=[p.parent.name for p in WP102])
def test_wp102_boards_tree_identical(path: Path) -> None:
    text = path.read_bytes().decode("utf-8", errors="replace")
    got = native.parse(text)
    assert got is not None
    assert same(got, sexpr.parse_py(text))


@pytest.mark.parametrize("path", DEMO_BOARDS, ids=[p.stem for p in DEMO_BOARDS])
def test_demo_boards_tree_identical(path: Path) -> None:
    text = path.read_bytes().decode("utf-8", errors="replace")
    got = native.parse(text)
    assert got is not None
    assert same(got, sexpr.parse_py(text))


@pytest.mark.parametrize("path", WP102, ids=[p.parent.name for p in WP102])
def test_wp102_content_hash_identical(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from oa_ingest_kicad import KiCadReader

    monkeypatch.setenv("OA_NATIVE", "1")
    h_native = KiCadReader().read(path).content_hash()
    monkeypatch.setenv("OA_NATIVE", "0")
    h_python = KiCadReader().read(path).content_hash()
    assert h_native == h_python


def test_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    import oa_native

    monkeypatch.setenv("OA_NATIVE", "0")
    assert oa_native.get() is None
    monkeypatch.setenv("OA_NATIVE", "1")
    assert oa_native.get() is not None
    monkeypatch.setenv("OA_NATIVE", "auto")
    assert oa_native.available()
