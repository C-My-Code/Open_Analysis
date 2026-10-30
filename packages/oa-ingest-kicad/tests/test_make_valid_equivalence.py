# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``fix_fractured`` (make_valid "structure" with a guard) against GEOS "linework" (WP-509).

Zone fills must be the same point set, with the same polygon and hole counts, as the linework
repair they replaced. Ring start vertices differ, so ``content_hash`` of boards with fractured
fills changed once, in WP-509.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
import shapely
from shapely.geometry import Polygon as ShPolygon

import oa_ingest_kicad.reader as reader_mod
from oa_ingest_kicad import KiCadReader
from oa_ingest_kicad._geom import fix_fractured, from_shapely, polygon_to_shapely

ROOT = Path(__file__).resolve().parents[3]
BOARDS = sorted((ROOT / "tests" / "boards" / "kicad10").glob("*/board.kicad_pcb"))
DEMOS = Path(os.environ.get("KICAD_DEMOS", r"D:\Program Files\KiCad\10.0\share\kicad\demos"))
DEMO_FILES = [
    DEMOS / "cm5_minima" / "CM5_MINIMA_3.kicad_pcb",
    DEMOS / "pic_programmer" / "pic_programmer.kicad_pcb",
]

# 10x10 square with two 2x2 holes joined to the outline by zero-width bridges (the bridges
# overlap collinear edges: "structure" alone loses the holes here, the area guard falls back)
_TWO_HOLES = np.array(
    [
        (0, 0), (10, 0), (10, 10), (0, 10), (0, 7), (1, 7), (1, 5), (3, 5), (3, 7), (1, 7),
        (0, 7), (0, 3), (1, 3), (1, 1), (3, 1), (3, 3), (1, 3), (0, 3),
    ],
    dtype=float,
)  # fmt: skip
_BOWTIE = np.array([(0, 0), (4, 4), (4, 0), (0, 4)], dtype=float)
_ONE_HOLE = np.array(
    [(0, 0), (10, 0), (10, 10), (0, 10), (0, 5), (3, 5), (3, 3), (6, 3), (6, 7), (3, 7), (3, 5), (0, 5)],
    dtype=float,
)  # fmt: skip


def _linework(ring: np.ndarray) -> object:
    p = ShPolygon(ring)
    return p if p.is_valid else shapely.make_valid(p)


def _shape(g: object) -> tuple[int, int, float]:
    polys = from_shapely(g)  # type: ignore[arg-type]
    return len(polys), sum(len(p.holes) for p in polys), float(shapely.area(g))


@pytest.mark.parametrize(
    "ring", [_TWO_HOLES, _BOWTIE, _ONE_HOLE], ids=["two_holes", "bowtie", "one_hole"]
)
def test_fix_fractured_matches_linework(ring: np.ndarray) -> None:
    got, ref = fix_fractured(ring), _linework(ring)
    assert shapely.symmetric_difference(got, ref).area == 0.0
    assert _shape(got)[:2] == _shape(ref)[:2]


def _read_both(path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[object, object]:
    new = KiCadReader().read(path)

    def old(ring: np.ndarray) -> object:
        return _linework(ring)

    monkeypatch.setattr(reader_mod, "fix_fractured", old)
    monkeypatch.setattr(reader_mod, "fix_fractured_many", lambda rings: [old(r) for r in rings])
    return new, KiCadReader().read(path)


def _assert_same_zones(new: object, old: object) -> None:
    assert len(new.zones) == len(old.zones)  # type: ignore[attr-defined]
    for zn, zo in zip(new.zones, old.zones, strict=True):  # type: ignore[attr-defined]
        assert (zn.net, [f.layer for f in zn.fills]) == (zo.net, [f.layer for f in zo.fills])
        for fn, fo in zip(zn.fills, zo.fills, strict=True):
            gn = shapely.union_all([polygon_to_shapely(p) for p in fn.polygons])
            go = shapely.union_all([polygon_to_shapely(p) for p in fo.polygons])
            assert shapely.symmetric_difference(gn, go).area <= 1e-18  # 1 nm^2
            assert len(fn.polygons) == len(fo.polygons)
            assert sum(len(p.holes) for p in fn.polygons) == sum(len(p.holes) for p in fo.polygons)
        assert (
            shapely.symmetric_difference(
                polygon_to_shapely(zn.outline), polygon_to_shapely(zo.outline)
            ).area
            <= 1e-18
        )


@pytest.mark.parametrize("path", BOARDS, ids=[p.parent.name for p in BOARDS])
def test_wp102_boards_equivalent(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_same_zones(*_read_both(path, monkeypatch))


@pytest.mark.kicad
@pytest.mark.parametrize("path", DEMO_FILES, ids=[p.stem for p in DEMO_FILES])
def test_demo_boards_equivalent(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not path.is_file():
        pytest.skip("KiCad demo boards not found")
    _assert_same_zones(*_read_both(path, monkeypatch))
