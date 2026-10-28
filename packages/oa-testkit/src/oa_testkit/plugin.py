# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""pytest plugin (entry point ``pytest11``): ``--update-golden``, markers and tool skipping.

Markers
-------
``golden``            reference comparisons.
``external(tool)``    needs an external tool (``openems``, ``palace``, ``kicad``...); skipped when
                      the tool is not found. Run nightly.
``slow``              takes more than 10 s.
``kicad``             needs KiCad 10 installed (``kicad-cli`` on PATH, ``$KICAD_CLI`` or the
                      default install location); skipped otherwise.

Must stay free of ``oa_model`` imports: it is loaded in every pytest session.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path

import pytest

from . import golden

_KICAD_DEFAULTS = (
    r"D:\Program Files\KiCad\10.0\bin\kicad-cli.exe",
    r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe",
    "/usr/bin/kicad-cli",
    "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
)


def tool_available(tool: str) -> bool:
    """Whether the logical external tool can be found on this machine."""
    t = tool.lower()
    if t in ("kicad", "kicad-cli"):
        if os.environ.get("KICAD_CLI") and Path(os.environ["KICAD_CLI"]).is_file():
            return True
        return shutil.which("kicad-cli") is not None or any(
            Path(p).is_file() for p in _KICAD_DEFAULTS
        )
    if t in ("openems", "openems-python"):
        return (
            importlib.util.find_spec("openEMS") is not None or shutil.which("openEMS") is not None
        )
    return shutil.which(t) is not None


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.getgroup("open_analysis").addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Rewrite the [expected] table of golden cases from actual values, do not compare.",
    )


def pytest_configure(config: pytest.Config) -> None:
    golden.set_update_mode(bool(config.getoption("--update-golden", default=False)))
    for line in (
        "golden: reference comparisons against tests/golden data",
        "external(tool): needs an external tool (openems, palace, kicad...); skipped when missing",
        "slow: takes more than 10 s",
        "kicad: needs KiCad 10 installed",
    ):
        config.addinivalue_line("markers", line)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        for mark in item.iter_markers("external"):
            for tool in mark.args:
                if not tool_available(str(tool)):
                    item.add_marker(pytest.mark.skip(reason=f"external tool '{tool}' not found"))
                    break
        if item.get_closest_marker("kicad") and not tool_available("kicad"):
            item.add_marker(pytest.mark.skip(reason="KiCad 10 (kicad-cli) not found"))


@pytest.fixture(scope="session")
def update_golden(request: pytest.FixtureRequest) -> bool:
    """True when pytest was run with ``--update-golden``."""
    return bool(request.config.getoption("--update-golden"))
