# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fixtures for WP-208 (run: ``uv run pytest tests/integration/ingest``)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import ingest_xval as xv


@pytest.fixture(scope="session")
def synthetic_results() -> dict[str, xv.BoardResult]:
    """Every WP-102 board, read natively and from its committed IPC-2581 export."""
    out: dict[str, xv.BoardResult] = {}
    for d in xv.synthetic_boards():
        ipc = d / "board.ipc2581.xml"
        out[d.name] = xv.run_board(
            d.name,
            d / "board.kicad_pcb",
            ipc if ipc.is_file() else None,
            xv.load_expected(d),
        )
    return out


@pytest.fixture(scope="session")
def demo_results(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, xv.BoardResult]:
    """The KiCad demo boards of ``xv.DEMOS``, native and via ``kicad-cli pcb export ipc2581``."""
    out: dict[str, xv.BoardResult] = {}
    work = tmp_path_factory.mktemp("demos")
    for name, directory, stem in xv.DEMOS:
        pcb = xv.demo_board(directory, stem)
        if pcb is None:
            continue
        out[name] = xv.run_board(name, pcb, xv.export_ipc(pcb, work / f"{name}.xml"), None)
    if not out:
        pytest.skip("KiCad demo boards not found")
    return out
