# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers for the impedance.profile tests (WP-102 boards, inline harness)."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml

from oa_geometry import BoardGeometry
from oa_impedance.profile import ImpedanceProfile
from oa_ingest_kicad import KiCadReader
from oa_model.board import Board
from oa_model.setup import AnalysisSetup
from oa_results.bundle import ResultBundle
from oa_results.profile import ProfileResult
from oa_testkit.harness import InlineRun, run_inline_full

BOARDS = Path(__file__).resolve().parents[4] / "tests" / "boards" / "kicad10"


@cache
def load_board(name: str) -> Board:
    return KiCadReader().read(BOARDS / name / "board.kicad_pcb")


def expected(name: str) -> dict[str, Any]:
    return yaml.safe_load((BOARDS / name / "expected.yaml").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def run_profile(
    board: Board, nets: tuple[str, ...] = (), services: Any = None, **cfg: Any
) -> InlineRun:
    config: dict[str, Any] = dict(cfg)
    if nets:
        config["nets"] = {"names": list(nets)}
    setup = AnalysisSetup(
        schema="oa-setup/1",  # type: ignore[arg-type,unused-ignore]
        name="profile",
        analysis="impedance.profile",
        config=config,
    )
    return run_inline_full(
        ImpedanceProfile, board, setup, geometry=BoardGeometry.build, services=services
    )


def profile_of(bundle: ResultBundle, name: str) -> ProfileResult:
    item = next(i for i in bundle.items if i.id == f"profile:{name}")
    assert isinstance(item.data, ProfileResult)
    return item.data


def table(bundle: ResultBundle, item_id: str) -> list[dict[str, Any]]:
    item = next(i for i in bundle.items if i.id == item_id)
    cols = [c.name for c in item.data.columns]  # type: ignore[union-attr]
    return [dict(zip(cols, r, strict=True)) for r in item.data.inline or ()]  # type: ignore[union-attr]
