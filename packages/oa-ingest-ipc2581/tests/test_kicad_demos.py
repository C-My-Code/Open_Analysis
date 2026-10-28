# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""IPC-2581 exports of KiCad demo boards against values recorded from pcbnew (golden)."""

from __future__ import annotations

import pytest
from board_stats import board_stats
from kicad_support import DEMOS, demo_board, export, reader_stackup

from oa_ingest_ipc2581 import Ipc2581Reader
from oa_model import validate_board
from oa_testkit.golden import assert_close_to_golden

pytestmark = [pytest.mark.kicad, pytest.mark.golden]


@pytest.mark.parametrize(("case", "directory", "stem"), DEMOS, ids=[d[0] for d in DEMOS])
def test_demo_board_matches_pcbnew(
    case: str, directory: str, stem: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    src = demo_board(directory, stem)
    if src is None:
        pytest.skip(f"demo board {directory}/{stem} not installed")
    ipc = export(src, tmp_path_factory.mktemp(case) / "board.xml")
    board = Ipc2581Reader().read(ipc)
    assert not [d for d in validate_board(board) if d.severity == "error"]
    stats = board_stats(board)
    actual = {**stats, "stackup": reader_stackup(board)}
    assert_close_to_golden(actual, "ingest_ipc2581", case)
    assert board.meta.source_tool == "ipc2581"
    assert board.meta.source_version == "IPC-2581C"
