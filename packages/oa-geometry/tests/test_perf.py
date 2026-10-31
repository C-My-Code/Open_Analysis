# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Performance targets (WP-104): 10k tracks < 10 s; 50k-segment benchmark under ``slow``."""

from __future__ import annotations

import time

import pytest
from synthetic import synthetic_board

from oa_geometry import BoardGeometry


def _run(n_nets: int, segs: int) -> tuple[int, dict[str, float]]:
    t0 = time.perf_counter()
    board = synthetic_board(n_nets, segs)
    t_board = time.perf_counter() - t0
    t0 = time.perf_counter()
    geo = BoardGeometry.build(board)
    geo.copper("L2")  # force the unions
    for lid in geo.layer_ids():
        geo.copper(lid)
    t_build = time.perf_counter() - t0
    t0 = time.perf_counter()
    rm = geo.reference_map()
    t_ref = time.perf_counter() - t0
    assert len(rm.spans) == len(board.tracks)
    assert all(sp for _, sp in rm.spans)
    # nearly every track has a reference (the rest sit on top of an antipad)
    referenced = sum(1 for _, sp in rm.spans if any(s.above or s.below for s in sp))
    assert referenced > 0.95 * len(rm.spans)
    return len(board.tracks), {"board": t_board, "build+copper": t_build, "reference_map": t_ref}


@pytest.mark.perf
def test_10k_tracks_build_and_reference_map_under_10s() -> None:
    n, t = _run(2000, 5)
    print(f"\n[perf] {n} tracks: {t}")
    assert t["build+copper"] + t["reference_map"] < 10.0


@pytest.mark.slow
@pytest.mark.perf
def test_50k_segments_benchmark() -> None:
    n, t = _run(10000, 5)
    print(f"\n[perf] {n} tracks: {t}")
    assert t["build+copper"] + t["reference_map"] < 120.0
