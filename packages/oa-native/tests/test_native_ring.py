# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ring predicates of the compiled core against the numpy oracle in ``oa_model.validate``."""

from __future__ import annotations

import numpy as np
import pytest

import oa_native
from oa_model.validate import _points_inside, _ring_self_intersects

native = oa_native.get()
pytestmark = pytest.mark.skipif(
    native is None or not hasattr(native, "ring_self_intersects"), reason="oa-native not built"
)


def _rings(rng: np.random.Generator) -> list[np.ndarray]:
    out: list[np.ndarray] = []
    for n in (3, 4, 5, 8, 13, 40, 120):
        for _ in range(60):
            out.append(rng.normal(size=(n, 2)) * 1e-3)  # random: mostly self-intersecting
        t = np.sort(rng.uniform(0.0, 2.0 * np.pi, size=n))  # star-shaped: simple
        r = rng.uniform(0.5, 1.0, size=n) * 1e-3
        out.append(np.stack((r * np.cos(t), r * np.sin(t)), axis=1))
    out.append(np.array([(0, 0), (4, 4), (4, 0), (0, 4)], dtype=float))  # bow-tie
    out.append(np.array([(0, 0), (1, 0), (1, 1), (0, 1)], dtype=float))
    return out


def test_ring_self_intersects_matches_oracle() -> None:
    rings = _rings(np.random.default_rng(7))
    got = [bool(native.ring_self_intersects(r)) for r in rings]
    assert got == [_ring_self_intersects(r) for r in rings]
    assert any(got) and not all(got)


def test_all_points_inside_matches_oracle() -> None:
    rng = np.random.default_rng(11)
    ring = np.array([(0, 0), (10, 0), (10, 10), (0, 10)], dtype=float) * 1e-3
    for _ in range(300):
        pts = rng.uniform(-2e-3, 12e-3, size=(int(rng.integers(1, 6)), 2))
        assert bool(native.all_points_inside(ring, pts)) == bool(_points_inside(ring, pts).all())
    # a concave ring, points on and next to vertices
    ring = np.array([(0, 0), (6, 0), (6, 6), (3, 2), (0, 6)], dtype=float)
    pts = np.array([(3, 1), (3, 2), (1, 5), (5, 5), (3, 5)], dtype=float)
    for i in range(len(pts)):
        p = pts[i : i + 1]
        assert bool(native.all_points_inside(ring, p)) == bool(_points_inside(ring, p).all())
