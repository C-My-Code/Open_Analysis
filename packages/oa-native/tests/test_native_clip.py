# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Clipping kernels (WP-509b) against the shapely/GEOS oracle."""

from __future__ import annotations

import numpy as np
import pytest

shapely = pytest.importorskip("shapely")
native = pytest.importorskip("oa_native")
mod = native.get()
if mod is None or not hasattr(mod, "cut_intervals"):
    pytest.skip("compiled oa-native not built", allow_module_level=True)


def _rings(geoms: list) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    xy, ring_len, geom_rings = [], [], []
    for g in geoms:
        n = 0
        for p in shapely.get_parts(g):
            for r in shapely.get_rings(p):
                c = np.asarray(r.coords)
                xy.append(c)
                ring_len.append(len(c))
                n += 1
        geom_rings.append(n)
    return (
        np.ascontiguousarray(np.vstack(xy)),
        np.concatenate(([0], np.cumsum(ring_len))).astype(np.int64),
        np.concatenate(([0], np.cumsum(geom_rings))).astype(np.int64),
    )


def _tpar(c: np.ndarray, seg: np.ndarray) -> np.ndarray:
    d = seg[1] - seg[0]
    return (c - seg[0]) @ d / float(d @ d)


def _oracle(seg: np.ndarray, geom) -> list[tuple[float, float]]:
    inter = shapely.intersection(shapely.linestrings(seg), geom)
    out = []
    for p in shapely.get_parts(inter):
        if shapely.get_type_id(p) == 1 and not shapely.is_empty(p):
            t = _tpar(np.asarray(p.coords), seg)
            out.append((float(t.min()), float(t.max())))
    return sorted(out)


def _plate():
    shell = [(0, 0), (10, 0), (10, 10), (0, 10)]
    holes = [[(2, 2), (4, 2), (4, 4), (2, 4)], [(6, 6), (8, 6), (8, 8), (6, 8)]]
    return shapely.Polygon(shell, holes)


def _merge(iv, tol=1e-9):
    out = []
    for lo, hi in sorted(iv):
        if out and lo <= out[-1][1] + tol:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def _native_x(lines_xy: np.ndarray, geoms: list, gi: np.ndarray) -> list[list[tuple[float, float]]]:
    xy, rs, gs = _rings(geoms)
    idx, val = mod.cut_intervals(lines_xy, gi.astype(np.int64), xy, rs, gs)
    res: list[list[tuple[float, float]]] = [[] for _ in range(len(lines_xy))]
    v = np.asarray(val).reshape(-1, 4)
    for i, row in zip(idx, v, strict=True):
        t = _tpar(row.reshape(2, 2), lines_xy[i])
        res[i].append((float(t.min()), float(t.max())))
    return res


def test_horizontal_cuts_through_holes_and_boundaries() -> None:
    plate = _plate()
    ys = [1.0, 3.0, 2.0, 4.0, 7.0, 0.0, 10.0, 5.0, 11.0]  # 2.0/4.0/0/10: along hole/outer edges
    lines = np.array([[[-1.0, y], [11.0, y]] for y in ys])
    got = _native_x(lines, [plate], np.zeros(len(ys), dtype=np.int64))
    for y, g, ln in zip(ys, got, lines, strict=True):
        want = _oracle(ln, plate)
        a, b = _merge(g), _merge(want)
        assert len(a) == len(b), y
        np.testing.assert_allclose(np.array(a), np.array(b), atol=1e-12, err_msg=str(y))


def test_random_segments_match_geos() -> None:
    rng = np.random.default_rng(509)
    plate = _plate()
    multi = shapely.MultiPolygon([plate, shapely.box(12, 0, 14, 3), shapely.box(14, 0, 16, 2)])
    geoms = [plate, multi]
    lines = rng.uniform(-2, 18, size=(400, 2, 2))
    gi = rng.integers(0, 2, size=400)
    got = _native_x(lines, geoms, gi)
    for ln, g, k in zip(lines, got, gi, strict=True):
        want = _oracle(ln, geoms[k])
        a = [(lo, hi) for lo, hi in _merge(g) if hi - lo > 1e-9]
        b = [(lo, hi) for lo, hi in _merge(want) if hi - lo > 1e-9]
        assert len(a) == len(b)
        np.testing.assert_allclose(np.array(a), np.array(b), atol=1e-9)


def test_rect_cover_edge_matches_geos() -> None:
    if not hasattr(mod, "rect_cover_edge"):
        pytest.skip("rect_cover_edge missing")
    rng = np.random.default_rng(510)
    plate = _plate()
    xy, rs, gs = _rings([plate])
    n = 300
    a = rng.uniform(0, 10, size=(n, 2))
    ang = rng.uniform(0, 2 * np.pi, size=n)
    ln = rng.uniform(0.2, 3.0, size=n)
    b = a + np.stack((np.cos(ang), np.sin(ang)), axis=1) * ln[:, None]
    seg = np.ascontiguousarray(np.stack((a, b), axis=1))
    rf = rng.uniform(0.05, 0.6, size=n)
    rb = rf * 0.4
    cap = np.full(n, 3.0)
    cov, dist = mod.rect_cover_edge(seg, rf, rb, cap, np.zeros(n, dtype=np.int64), xy, rs, gs)
    bnd = shapely.boundary(plate)
    checked = 0
    for i in range(n):
        lines = shapely.linestrings(seg[i])
        foot = shapely.buffer(lines, rf[i], cap_style="flat", join_style="round")
        want = bool(shapely.covers(plate, foot))
        assert bool(cov[i]) == want, i
        if want:
            body = shapely.buffer(lines, rb[i], cap_style="flat", join_style="round")
            d = min(float(shapely.distance(body, bnd)), cap[i])
            assert dist[i] == pytest.approx(d, rel=1e-9, abs=1e-12)
            checked += 1
    assert checked > 20
