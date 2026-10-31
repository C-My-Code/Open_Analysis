# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""WP-520: the ReferenceMap paths that were quadratic on a plane with one antipad per via.

No wall clock: the tests check that the work handed to the kernels does not grow with the
number of holes, and that the shortened paths give the answer of the full-geometry oracle.
"""

from __future__ import annotations

import numpy as np
import pytest
import shapely
from synthetic import synthetic_board

from oa_geometry import BoardGeometry
from oa_geometry._native import RingSet
from oa_geometry._native import kernels as native_kernels
from oa_geometry.refmap import _covers_part, _ref_layer
from oa_geometry.topology import items_by_net


def _plane(n: int, seed: int = 1) -> shapely.Polygon:
    """10 x 10 plate with an n x n grid of square antipads (the bulk of the ring vertices)."""
    pitch = 10.0 / (n + 1)
    holes = []
    for i in range(n):
        for j in range(n):
            x, y = (i + 1) * pitch, (j + 1) * pitch
            r = 0.2 * pitch
            holes.append([(x - r, y - r), (x + r, y - r), (x + r, y + r), (x - r, y + r)])
    return shapely.Polygon([(0, 0), (10, 0), (10, 10), (0, 10)], holes)


def _segments(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    a = rng.uniform(0.2, 9.8, size=(n, 2))
    d = rng.normal(size=(n, 2))
    d /= np.linalg.norm(d, axis=1)[:, None]
    b = np.clip(a + d * rng.uniform(0.05, 1.5, size=(n, 1)), 0.0, 10.0)
    # a few exactly axis-aligned segments that run along hole edges
    a[:5, 1] = b[:5, 1] = 10.0 / 21 * 3 + 0.2 * 10.0 / 21
    return np.stack((a, b), axis=1)


def _boxes(seg: np.ndarray, r: np.ndarray, cap: np.ndarray) -> np.ndarray:
    ln = np.hypot(*(seg[:, 1] - seg[:, 0]).T)
    ext = np.abs(np.stack((seg[:, 1, 1] - seg[:, 0, 1], seg[:, 1, 0] - seg[:, 0, 0]), axis=1))
    ext = ext / ln[:, None] * r[:, None]
    lo = np.minimum(seg[:, 0], seg[:, 1]) - ext - cap[:, None] - 1e-9
    hi = np.maximum(seg[:, 0], seg[:, 1]) + ext + cap[:, None] + 1e-9
    return np.concatenate((lo, hi), axis=1)


def test_ring_subset_is_small_and_local() -> None:
    plane = _plane(20)  # 400 holes
    rings = RingSet(np.array([plane], dtype=object))
    seg = _segments(50, 3)
    r = np.full(50, 0.05)
    box = _boxes(seg, r, np.full(50, 0.1))
    total = 0
    for q, sub in rings.near(np.zeros(50, dtype=np.int64), box, max_vertices=200):
        assert len(sub.geom_start) == len(q) + 1
        total += len(sub.ring_start) - 1
    # each query sees the shell and the few antipads near it, never all 401 rings
    assert total < 50 * 12


def test_rect_cover_edge_on_ring_subset_equals_whole_geometry() -> None:
    kern = native_kernels()
    if kern is None:
        pytest.skip("compiled oa-native not built")
    plane = _plane(12)
    other = shapely.Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    rings = RingSet(np.array([plane, other], dtype=object))
    n = 400
    seg = _segments(n, 7)
    r_foot = np.random.default_rng(2).uniform(0.01, 0.4, size=n)
    r_body = r_foot / 3.0
    cap = np.random.default_rng(3).uniform(0.05, 0.6, size=n)
    geom_of = (np.arange(n) % 2).astype(np.int64)
    full = kern.rect_cover_edge(
        seg, r_foot, r_body, cap, geom_of, rings.xy, rings.ring_start, rings.geom_start
    )
    cov = np.zeros(n, dtype=bool)
    dist = np.zeros(n)
    for q, sub in rings.near(geom_of, _boxes(seg, r_foot, cap), max_vertices=500):
        c, d = kern.rect_cover_edge(
            np.ascontiguousarray(seg[q]),
            np.ascontiguousarray(r_foot[q]),
            np.ascontiguousarray(r_body[q]),
            np.ascontiguousarray(cap[q]),
            np.arange(len(q), dtype=np.int64),
            sub.xy,
            sub.ring_start,
            sub.geom_start,
        )
        cov[q], dist[q] = c, d
    assert cov.tolist() == list(full[0])
    assert dist.tolist() == list(full[1])
    assert 0 < cov.sum() < n


def test_covers_part_equals_geos_covers() -> None:
    board = synthetic_board(40, 5)
    geo = BoardGeometry.build(board)
    checked = 0
    for lid in geo.layer_ids():
        rl = _ref_layer(geo, lid)
        if rl is None or rl.hole_tree is None:
            continue
        rng = np.random.default_rng(5)
        (x0, y0, x1, y1) = shapely.total_bounds(rl.parts)
        n = 600
        c = rng.uniform((x0, y0), (x1, y1), size=(n, 2))
        hw = rng.uniform(5e-5, 1.5e-3, size=n)
        foot = shapely.box(c[:, 0] - hw, c[:, 1] - hw / 2, c[:, 0] + hw, c[:, 1] + hw / 2)
        # footprints sharing an edge with an antipad (touching is covered, overlapping is not)
        hb = shapely.bounds(rl.holes[:50])
        touch = shapely.box(hb[:, 2], hb[:, 1], hb[:, 2] + 1e-4, hb[:, 3])
        foot = np.concatenate((foot, touch, shapely.buffer(rl.holes[:50], 1e-5)))
        parts = rng.integers(0, len(rl.parts), size=len(foot))
        parts[n:] = rl.hole_part[np.r_[0:50, 0:50]]
        expected = shapely.covers(rl.parts[parts], foot)
        got = _covers_part(rl, parts.astype(np.intp), foot)
        assert got.tolist() == expected.tolist()
        assert got.any() and not got.all()
        checked += 1
    assert checked


def test_items_by_net_keeps_board_order() -> None:
    board = synthetic_board(30, 5)
    geo = BoardGeometry.build(board)
    pads, vias = items_by_net(geo)
    for net in {p.net for p in board.pads} | {v.net for v in board.vias}:
        if net is None:
            continue
        assert pads.get(net, []) == [p for p in board.pads if p.net == net]
        assert vias.get(net, []) == [v for v in board.vias if v.net == net]
