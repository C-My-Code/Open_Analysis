# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers for the oa-erc tests: WP-102 boards, expected.yaml, a shared service locator."""

from __future__ import annotations

from functools import cache, lru_cache
from pathlib import Path
from typing import Any

import yaml

from oa_erc import run_rules
from oa_framework.registry import Registry
from oa_ingest_kicad import KiCadReader
from oa_model.board import Board
from oa_model.setup import ReadOptions
from oa_model.setup.intent import DesignIntent, NetSelector, SignalIntent
from oa_results.violations import Violation

BOARDS = Path(__file__).resolve().parents[3] / "tests" / "boards" / "kicad10"
BOARD_NAMES = (
    "combined",
    "cpwg",
    "diff_pair",
    "microstrip",
    "microstrip_v9",
    "power_plane",
    "power_strip",
    "power_two_layer_vias",
    "split_plane",
    "stripline",
    "via_transition",
)


@lru_cache(maxsize=1)
def services() -> Any:
    return Registry.load().services()


@cache
def load_board(name: str) -> Board:
    return KiCadReader().read(BOARDS / name / "board.kicad_pcb", ReadOptions())


def expected(name: str) -> dict[str, Any]:
    data = yaml.safe_load((BOARDS / name / "expected.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


# The standalone microstrip boards carry no net class: the 50 ohm target comes from intent
# (the board README: "IM-002 on the 0.25 and 0.5 mm traces").
_MS_INTENT = DesignIntent(
    signals=(SignalIntent(select=NetSelector(names=("MS_*",)), target_z_se=50.0),)
)
INTENTS = {"microstrip": _MS_INTENT, "microstrip_v9": _MS_INTENT}


@cache
def run_board(name: str) -> tuple[Violation, ...]:
    return tuple(run_rules(load_board(name), intent=INTENTS.get(name), services=services()))


def loc_mm(v: Violation) -> tuple[float, float]:
    """Violation location in the KiCad file frame (mm, Y down)."""
    return (v.location[0] * 1e3, -v.location[1] * 1e3)
