# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""WP-520: a missing or stale compiled module is reported, never silent."""

from __future__ import annotations

import logging
import sys
import types

import pytest

import oa_native


@pytest.fixture(autouse=True)
def _fresh_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oa_native, "_warned", set())
    monkeypatch.setenv("OA_NATIVE", "auto")


def test_missing_module_is_logged_and_reported(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setitem(sys.modules, "oa_native._native", None)  # import raises ImportError
    with caplog.at_level(logging.WARNING, logger="oa_native"):
        assert oa_native.get() is None
        assert oa_native.get() is None
    msgs = [r.getMessage() for r in caplog.records if r.name == "oa_native"]
    assert len(msgs) == 1 and "pure-Python" in msgs[0]
    st = oa_native.status()
    assert st["available"] is False and st["fallback"] is True
    assert st["missing"] == list(oa_native.KERNELS)


def test_stale_module_is_logged_and_reported(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    stale = types.ModuleType("oa_native._native")
    stale.parse = lambda text: None  # type: ignore[attr-defined]
    stale.kernel_version = lambda: 1  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "oa_native._native", stale)
    with caplog.at_level(logging.WARNING, logger="oa_native"):
        assert oa_native.get() is stale
    msgs = [r.getMessage() for r in caplog.records if r.name == "oa_native"]
    assert len(msgs) == 1 and "stale" in msgs[0] and "rect_cover_edge" in msgs[0]
    st = oa_native.status()
    assert st["available"] is True and st["stale"] is True and st["fallback"] is True
    assert "parse" not in st["missing"] and "bem_influence" in st["missing"]
    assert st["kernel_version"] == 1


def test_disabled_is_not_a_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OA_NATIVE", "0")
    monkeypatch.setitem(sys.modules, "oa_native._native", None)
    assert oa_native.get() is None
    assert oa_native.status()["fallback"] is False


def test_status_of_this_build() -> None:
    st = oa_native.status()
    assert set(st) >= {"mode", "available", "stale", "missing", "fallback", "path"}
    if st["available"]:
        # the module built from this source tree has every kernel (else `uv sync` is stale)
        assert st["missing"] == [], st
