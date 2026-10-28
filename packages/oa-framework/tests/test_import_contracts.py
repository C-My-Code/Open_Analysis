# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""WP-000 acceptance: the import-linter contracts catch forbidden imports.

A throwaway contract on a temp package proves that an ``oa_erc`` -> ``oa_pi`` import is
reported, using the same ``independence`` contract type as the real configuration. A second
test checks that the real root configuration contains the contracts interfaces.md section 0
requires.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

_RUN = "from importlinter.cli import lint_imports_command; lint_imports_command()"

_CONTRACT = """\
[importlinter]
root_packages =
    oa_erc
    oa_pi

[importlinter:contract:independence]
name = L4 analyses are independent
type = independence
modules =
    oa_erc
    oa_pi
"""


def _lint(tmp: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _RUN, "--config", str(tmp / ".importlinter")],
        cwd=tmp,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(tmp), "PATH": "", "SYSTEMROOT": "C:\\Windows"},
        check=False,
    )


def _make_pkgs(tmp: Path, erc_source: str) -> None:
    for name, src in (("oa_erc", erc_source), ("oa_pi", "VALUE = 1\n")):
        pkg = tmp / name
        pkg.mkdir()
        (pkg / "__init__.py").write_text(src, encoding="utf-8")
    (tmp / ".importlinter").write_text(_CONTRACT, encoding="utf-8")


def test_forbidden_import_is_caught(tmp_path: Path) -> None:
    pytest.importorskip("importlinter")
    _make_pkgs(tmp_path, "from oa_pi import VALUE\n")
    result = _lint(tmp_path)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "BROKEN" in result.stdout
    assert "oa_erc" in result.stdout and "oa_pi" in result.stdout


def test_clean_packages_pass(tmp_path: Path) -> None:
    pytest.importorskip("importlinter")
    _make_pkgs(tmp_path, "VALUE = 2\n")
    result = _lint(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def _root_config() -> dict[str, object]:
    root = Path(__file__).resolve()
    while not (root / "pyproject.toml").exists() or "importlinter" not in (
        root / "pyproject.toml"
    ).read_text(encoding="utf-8"):
        if root.parent == root:
            pytest.skip("root pyproject.toml not found")
        root = root.parent
    with (root / "pyproject.toml").open("rb") as fh:
        data: dict[str, object] = tomllib.load(fh)
    return data


def test_root_config_has_required_contracts() -> None:
    tool = _root_config()["tool"]
    assert isinstance(tool, dict)
    contracts = tool["importlinter"]["contracts"]
    by_type = {c["type"] for c in contracts}
    assert {"layers", "independence", "forbidden"} <= by_type
    layers = next(c for c in contracts if c["type"] == "layers")["layers"]
    assert layers[0] == "oa_assistant"  # nothing may import the AI assistant
    assert "oa_cli | oa_server | oa_mcp" in layers
    independence = next(c for c in contracts if c["type"] == "independence")
    assert {"oa_erc", "oa_pi", "oa_impedance"} <= set(independence["modules"])
