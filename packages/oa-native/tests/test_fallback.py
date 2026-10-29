# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""With no compiled module, everything stays on Python (WP-508 acceptance)."""

from __future__ import annotations

import sys

import pytest

from oa_ingest_kicad import sexpr


def test_parse_without_native_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OA_NATIVE", "auto")
    # a None entry makes `import oa_native` raise ImportError
    monkeypatch.setitem(sys.modules, "oa_native", None)
    monkeypatch.setitem(sys.modules, "oa_native._native", None)
    assert sexpr.parse("(a (b 1))") == ["a", ["b", "1"]]


def test_native_required_but_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OA_NATIVE", "1")
    monkeypatch.setitem(sys.modules, "oa_native", None)
    with pytest.raises(ImportError):
        sexpr.parse("(a)")


def test_native_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OA_NATIVE", "0")
    assert sexpr.parse("(a (b 1))") == ["a", ["b", "1"]]
