# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Golden helper, TOML round trip and the pytest plugin (run in a subprocess)."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
import tomllib
from pathlib import Path

import numpy as np
import pytest

from oa_testkit import golden
from oa_testkit.golden import GoldenError, Tolerance, assert_close_to_golden, write_case


def _make(root: Path, expected: dict, **tol: object) -> None:
    write_case(
        root / "suite" / "c1" / "case.toml",
        source="analytic: x = 1",
        tolerance=Tolerance(**tol),  # type: ignore[arg-type]
        inputs={"a": 1.5, "name": "n"},
        expected=expected,
    )


def test_pass_and_fail(tmp_path: Path) -> None:
    _make(tmp_path, {"z0": 50.0, "arr": [1.0, 2.0]}, rtol=1e-3)
    assert_close_to_golden(
        {"z0": 50.01, "arr": np.array([1.0, 2.001])}, "suite", "c1", root=tmp_path
    )
    with pytest.raises(GoldenError, match="z0"):
        assert_close_to_golden({"z0": 51.0, "arr": [1.0, 2.0]}, "suite", "c1", root=tmp_path)
    with pytest.raises(GoldenError, match="arr"):
        assert_close_to_golden({"z0": 50.0, "arr": [1.0, 2.1]}, "suite", "c1", root=tmp_path)
    with pytest.raises(GoldenError, match="missing"):
        assert_close_to_golden({"arr": [1.0, 2.0]}, "suite", "c1", root=tmp_path)
    with pytest.raises(GoldenError, match="shape"):
        assert_close_to_golden({"z0": 50.0, "arr": [1.0]}, "suite", "c1", root=tmp_path)


def test_call_tolerance_override(tmp_path: Path) -> None:
    _make(tmp_path, {"z0": 50.0}, rtol=1e-9)
    with pytest.raises(GoldenError):
        assert_close_to_golden({"z0": 50.5}, "suite", "c1", root=tmp_path)
    assert_close_to_golden({"z0": 50.5}, "suite", "c1", root=tmp_path, rtol=0.02)


def test_per_key_tolerance(tmp_path: Path) -> None:
    write_case(
        tmp_path / "s" / "c" / "case.toml",
        source="s",
        tolerance=Tolerance(1e-9, 0.0, {"loose": Tolerance(0.1)}),
        inputs={},
        expected={"tight": 1.0, "loose": 1.0},
    )
    assert_close_to_golden({"tight": 1.0, "loose": 1.05}, "s", "c", root=tmp_path)
    with pytest.raises(GoldenError, match="tight"):
        assert_close_to_golden({"tight": 1.05, "loose": 1.0}, "s", "c", root=tmp_path)


def test_update_rewrites_expected_only(tmp_path: Path) -> None:
    _make(tmp_path, {"z0": 50.0}, rtol=1e-4)
    assert_close_to_golden(
        {"z0": 49.0, "c": np.array([1 + 2j])}, "suite", "c1", root=tmp_path, update=True
    )
    data = tomllib.loads((tmp_path / "suite" / "c1" / "case.toml").read_text("utf-8"))
    assert data["source"] == "analytic: x = 1"
    assert data["tolerance"]["rtol"] == 1e-4
    assert data["inputs"] == {"a": 1.5, "name": "n"}
    assert data["expected"] == {"z0": 49.0, "c": {"re": [1.0], "im": [2.0]}}
    assert_close_to_golden(
        {"z0": 49.0, "c": {"re": [1.0], "im": [2.0]}}, "suite", "c1", root=tmp_path
    )


def test_update_new_case_needs_source(tmp_path: Path) -> None:
    with pytest.raises(GoldenError, match="source"):
        assert_close_to_golden({"x": 1.0}, "s", "new", root=tmp_path, update=True)
    assert_close_to_golden(
        {"x": 1.0}, "s", "new", root=tmp_path, update=True, source="analytic: x=1"
    )
    assert_close_to_golden({"x": 1.0}, "s", "new", root=tmp_path)


def test_missing_case_message(tmp_path: Path) -> None:
    with pytest.raises(GoldenError, match="--update-golden"):
        assert_close_to_golden({"x": 1.0}, "s", "nope", root=tmp_path)


def test_toml_round_trip_types(tmp_path: Path) -> None:
    exp = {
        "i": 3,
        "f": 0.1 + 0.2,
        "b": True,
        "s": 'q"uote',
        "nested": {"k": [1.0, [2.0, 3.0]], "deep": {"x": float("inf")}},
        "weird key": 1.0,
    }
    path = tmp_path / "s" / "c" / "case.toml"
    write_case(path, source="s", tolerance=Tolerance(), inputs={}, expected=exp)
    got = golden.load_case("s", "c", root=tmp_path).expected
    assert got["f"] == 0.1 + 0.2
    assert got["nested"]["deep"]["x"] == float("inf")
    assert got["s"] == 'q"uote'
    assert got["weird key"] == 1.0


def test_golden_root_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OA_GOLDEN_DIR", str(tmp_path))
    assert golden.golden_root() == tmp_path


# ---- plugin, exercised in a fresh interpreter so entry-point loading is covered ----


def _plugin_args() -> list[str]:
    eps = importlib.metadata.entry_points(group="pytest11")
    return [] if any(ep.value == "oa_testkit.plugin" for ep in eps) else ["-p", "oa_testkit.plugin"]


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *_plugin_args(), *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )


_TEST_SRC = """
import pytest
from oa_testkit.golden import assert_close_to_golden

@pytest.mark.golden
def test_g():
    assert_close_to_golden({"x": 2.0}, "s", "c", source="analytic: x = 2")

@pytest.mark.external("definitely-not-a-real-tool")
def test_ext():
    raise AssertionError("must be skipped")

@pytest.mark.kicad
@pytest.mark.slow
def test_k(): pass
"""


def test_plugin_markers_and_update_golden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OA_GOLDEN_DIR", str(tmp_path / "gold"))
    (tmp_path / "test_x.py").write_text(_TEST_SRC, encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --strict-markers\n", encoding="utf-8")

    first = _run(tmp_path, "-q", "-rs", "-k", "test_g or test_ext")
    assert "1 failed" in first.stdout  # no case yet, not in update mode
    assert "1 skipped" in first.stdout  # unknown external tool

    second = _run(tmp_path, "-q", "--update-golden", "-k", "test_g")
    assert second.returncode == 0, second.stdout + second.stderr
    assert (tmp_path / "gold" / "s" / "c" / "case.toml").is_file()

    third = _run(tmp_path, "-q", "-k", "test_g")
    assert third.returncode == 0, third.stdout + third.stderr

    markers = _run(tmp_path, "--markers")
    for name in ("golden", "external(tool)", "slow", "kicad"):
        assert name in markers.stdout
