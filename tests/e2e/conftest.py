# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fixtures of the CLI end-to-end suite: one staged, ``oa init``-ed project per board."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_here = str(Path(__file__).resolve().parent)
if _here not in sys.path:
    sys.path.insert(0, _here)

from e2e_helpers import BOARDS, CM5, Cli, Project, stage_board, wp102_boards  # noqa: E402

CM5_NAME = "cm5_minima"


def _params() -> list[Any]:
    params: list[Any] = [pytest.param(n, id=n) for n in wp102_boards()]
    params.append(
        pytest.param(
            CM5_NAME,
            id=CM5_NAME,
            marks=[
                pytest.mark.slow,
                pytest.mark.skipif(not CM5.is_file(), reason="KiCad demo cm5_minima not found"),
            ],
        )
    )
    return params


@pytest.fixture(scope="module", params=_params())
def project(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Project:
    name: str = request.param
    source = CM5 if name == CM5_NAME else BOARDS / name / "board.kicad_pcb"
    work = tmp_path_factory.mktemp(name)
    board = stage_board(source, work)
    cli = Cli(work)
    init = cli.run("init", "--yes", ".")
    return Project(name=name, cli=cli, board=board, init=init)
