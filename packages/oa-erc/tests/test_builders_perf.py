# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Builder boards: negative fixtures, injected single violations, and the 10k-track timing."""

from __future__ import annotations

import time

import pytest
from erc_helpers import services
from erc_synthetic import many_tracks_board

from oa_erc import run_rules
from oa_geometry import BoardGeometry
from oa_model.setup.intent import DesignIntent, NetSelector, SignalIntent
from oa_testkit import builders as tb

_Z50 = DesignIntent(signals=(SignalIntent(select=NetSelector(names=("SIG",)), target_z_se=50.0),))


def _ids(board, **kw):  # type: ignore[no-untyped-def]
    return [(v.rule_id, v.nets) for v in run_rules(board, services=services(), **kw)]


@pytest.mark.parametrize(
    "board",
    [tb.microstrip_board(), tb.diff_pair_board(), tb.power_plane_board()],
    ids=["microstrip", "diff_pair", "power_plane"],
)
def test_clean_builder_boards_report_nothing(board) -> None:  # type: ignore[no-untyped-def]
    assert _ids(board) == []


def test_injected_wide_trace_reports_only_im002() -> None:
    assert _ids(tb.microstrip_board(width=0.6e-3)) == [("IM-002", ("SIG",))]


def test_injected_reference_change_reports_only_rp003() -> None:
    assert _ids(tb.split_plane_board()) == [("RP-003", ("SIG",))]


def test_injected_fence_reports_only_rf001() -> None:
    assert [r for r, _ in _ids(tb.cpwg_board())] == ["RF-001"]


def test_negative_fixture_in_tolerance_microstrip() -> None:
    assert _ids(tb.microstrip_board(width=0.38e-3), intent=_Z50) == []


@pytest.mark.perf
def test_all_rules_on_10k_tracks_under_60s() -> None:
    board = many_tracks_board(1000, 10)
    assert len(board.tracks) == 10_000
    intent = DesignIntent(
        signals=(SignalIntent(select=NetSelector(names=("N*",)), target_z_se=50.0),)
    )
    t0 = time.perf_counter()
    geo = BoardGeometry.build(board)
    vs = run_rules(board, intent=intent, services=services(), geometry=geo)
    dt = time.perf_counter() - t0
    print(f"10k tracks, all rules: {dt:.1f} s, {len(vs)} violations")
    assert dt < 60.0
