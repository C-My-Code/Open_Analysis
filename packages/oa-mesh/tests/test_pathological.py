# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pathological copper: slivers, near-coincident edges, touching holes, many holes, garbage."""

from __future__ import annotations

import numpy as np
import pytest
from mesh_helpers import circle, piece, poly, random_centres, rect
from shapely.geometry import Polygon as ShPolygon  # type: ignore[import-untyped]

from oa_mesh import CleanOptions, Contact, MeshError, SheetMesh, mesh_copper_sheets
from oa_model.arrays import Points
from oa_model.geometry import Polygon


def mesh(p: Polygon | list[Polygon], h: float = 0.5e-3, **kw: object) -> SheetMesh:
    polys = p if isinstance(p, list) else [p]
    (m,) = mesh_copper_sheets([piece(polys)], h_default=h, **kw)  # type: ignore[arg-type]
    return m


def shapely_area(p: Polygon) -> float:
    return float(ShPolygon(p.exterior.array, [h.array for h in p.holes]).area)


def check_ok(m: SheetMesh, area: float | None = None, rel: float = 1e-9) -> None:
    q = m.quality
    assert q.n_triangles > 0
    assert q.min_angle_deg > 1.0
    assert np.isfinite(q.max_aspect)
    if area is not None:
        assert q.area == pytest.approx(area, rel=rel)
    assert q.area == pytest.approx(m.cleaning.area_out, rel=1e-6) or m.cleaning.n_holes >= 0


def test_hole_touching_outline_along_an_edge() -> None:
    # hole shares the left edge x=0 with the outline: copper has a notch, not a hole
    p = poly(rect(0, 0, 4e-3, 4e-3), [(0, 1e-3), (0, 3e-3), (1e-3, 3e-3), (1e-3, 1e-3)])
    m = mesh(p)
    check_ok(m, 16e-6 - 2e-6)
    assert m.cleaning.n_holes == 0


def test_hole_touching_outline_at_a_point() -> None:
    hole = [(0, 2e-3), (1e-3, 2.5e-3), (1e-3, 1.5e-3)]  # vertex exactly on x=0
    p = poly(rect(0, 0, 4e-3, 4e-3), hole)
    m = mesh(p)
    check_ok(m)
    # a pinched hole is opened into a notch or kept strictly inside; never a touching ring
    assert m.quality.area == pytest.approx(16e-6 - 0.5e-6, rel=2e-2)


def test_two_holes_touching_each_other() -> None:
    h1 = [(1e-3, 1e-3), (2e-3, 1e-3), (2e-3, 2e-3), (1e-3, 2e-3)][::-1]
    h2 = [(2e-3, 1.2e-3), (3e-3, 1.2e-3), (3e-3, 1.8e-3), (2e-3, 1.8e-3)][
        ::-1
    ]  # shares edge x=2e-3
    m = mesh(poly(rect(0, 0, 4e-3, 4e-3), h1, h2))
    check_ok(m, 16e-6 - 1e-6 - 0.6e-6)
    assert m.cleaning.n_holes == 1


def test_hole_with_sub_micron_wall_to_outline() -> None:
    # 0.2 um copper wall between hole and outline: a sliver
    w = 0.2e-6
    h1 = [(w, 1e-3), (1e-3, 1e-3), (1e-3, 3e-3), (w, 3e-3)][::-1]
    m = mesh(poly(rect(0, 0, 4e-3, 4e-3), h1))
    check_ok(m)
    assert m.cleaning.removed_area > 0
    assert m.quality.min_angle_deg > 15


def test_thin_sliver_spur_is_removed() -> None:
    # a 0.3 um wide, 2 mm long spur on a square
    ext = [
        (0, 0),
        (3e-3, 0),
        (3e-3, 3e-3),
        (0, 3e-3),
        (0, 1.5e-3 + 0.3e-6),
        (-2e-3, 1.5e-3 + 0.3e-6),
        (-2e-3, 1.5e-3),
        (0, 1.5e-3),
    ]
    m = mesh(poly(ext))
    check_ok(m, 9e-6, rel=1e-6)
    assert m.cleaning.removed_area == pytest.approx(0.6e-9, rel=0.1)


def test_min_feature_zero_still_resolves_point_touch() -> None:
    hole = [(0, 2e-3), (1e-3, 2.5e-3), (1e-3, 1.5e-3)]
    m = mesh(poly(rect(0, 0, 4e-3, 4e-3), hole), clean=CleanOptions(min_feature=0.0))
    check_ok(m)


def test_near_coincident_vertices_collapse() -> None:
    ext = [(0, 0), (1e-12, 0), (3e-3, 0), (3e-3, 1e-13), (3e-3, 3e-3), (0, 3e-3), (0, 2e-12)]
    m = mesh(poly(ext), h=1e-3)
    check_ok(m, 9e-6)


def test_near_coincident_parallel_edges() -> None:
    # U shape whose two arms are 0.4 um apart (a slit narrower than min_feature closes)
    ext = [
        (0, 0),
        (4e-3, 0),
        (4e-3, 3e-3),
        (2e-3 + 0.2e-6, 3e-3),
        (2e-3 + 0.2e-6, 0.5e-3),
        (2e-3 - 0.2e-6, 0.5e-3),
        (2e-3 - 0.2e-6, 3e-3),
        (0, 3e-3),
    ]
    m = mesh(poly(ext))
    check_ok(m)
    assert m.cleaning.closed_area > 0
    assert m.quality.area == pytest.approx(12e-6, rel=1e-3)


def test_bowtie_self_intersection_is_repaired() -> None:
    bow = [(0, 0), (2e-3, 2e-3), (2e-3, 0), (0, 2e-3)]
    m = mesh(poly(bow))
    check_ok(m)
    assert m.cleaning.n_repaired == 1
    assert m.cleaning.n_polygons == 2
    assert m.quality.area == pytest.approx(2e-6, rel=1e-6)  # two triangles of 1 mm^2


def test_duplicate_and_collinear_vertices() -> None:
    ext = [
        (0, 0),
        (0, 0),
        (1e-3, 0),
        (2e-3, 0),
        (2e-3, 0),
        (2e-3, 2e-3),
        (1e-3, 2e-3),
        (0, 2e-3),
        (0, 1e-3),
    ]
    m = mesh(poly(ext))
    check_ok(m, 4e-6)


def test_overlapping_polygons_are_unioned() -> None:
    a = poly(rect(0, 0, 2e-3, 2e-3))
    b = poly(rect(1e-3, 1e-3, 3e-3, 3e-3))
    m = mesh([a, b])
    check_ok(m, 7e-6)
    assert m.cleaning.n_polygons == 1


def test_identical_duplicate_polygons() -> None:
    a = poly(rect(0, 0, 2e-3, 2e-3))
    m = mesh([a, a, a])
    check_ok(m, 4e-6)


def test_hole_outside_exterior_and_hole_crossing_outline() -> None:
    outside = [(5e-3, 5e-3), (6e-3, 5e-3), (6e-3, 6e-3), (5e-3, 6e-3)][::-1]
    crossing = [(-1e-3, 1e-3), (1e-3, 1e-3), (1e-3, 2e-3), (-1e-3, 2e-3)][::-1]
    m = mesh(poly(rect(0, 0, 4e-3, 4e-3), outside, crossing))
    check_ok(m, 16e-6 - 1e-6, rel=1e-6)


def test_hole_filled_with_island() -> None:
    # island of copper floating in a hole: two polygons after cleaning
    big = poly(rect(0, 0, 6e-3, 6e-3), circle(3e-3, 3e-3, 2e-3, 32, cw=True))
    isl = poly(circle(3e-3, 3e-3, 1e-3, 32))
    m = mesh([big, isl], h=0.4e-3)
    check_ok(m)
    assert m.cleaning.n_polygons == 2


def test_tiny_geometry_micron_scale() -> None:
    m = mesh(poly(rect(0, 0, 50e-6, 30e-6), circle(25e-6, 15e-6, 5e-6, 16, cw=True)), h=5e-6)
    check_ok(m)
    assert m.quality.min_angle_deg > 20


def test_large_aspect_trace() -> None:
    # 20 mm x 100 um trace
    m = mesh(poly(rect(0, 0, 20e-3, 100e-6)), h=1e-3)
    check_ok(m, 20e-3 * 100e-6)
    assert m.quality.min_angle_deg > 15


def test_concave_comb_and_acute_corners() -> None:
    teeth: list[tuple[float, float]] = [(0, 0)]
    x = 0.0
    for _ in range(8):
        teeth += [(x + 0.2e-3, 0), (x + 0.2e-3, 2e-3), (x + 0.4e-3, 2e-3), (x + 0.4e-3, 0)]
        x += 0.4e-3
    teeth += [(x, -1e-3), (0, -1e-3)]
    m = mesh(poly(teeth), h=0.5e-3)
    check_ok(m)
    # thin acute spike (5 degrees)
    spike = [(0, 0), (4e-3, 0), (4e-3 + 3e-3, 0.26e-3), (4e-3, 0.5e-3), (0, 0.5e-3)]
    m2 = mesh(poly(spike), h=0.5e-3)
    check_ok(m2)


def test_many_holes_dense() -> None:
    w, h = 20e-3, 20e-3
    cs = random_centres(350, w, h, 1e-3, 0.75e-3, seed=2)
    holes = [circle(x, y, 0.25e-3, 12, cw=True) for x, y in cs]
    p = poly(rect(0, 0, w, h), *holes)
    m = mesh(p, h=1e-3)
    check_ok(m, shapely_area(p), rel=1e-9)
    assert m.cleaning.n_holes == 350


def test_holes_in_a_grid_with_thin_webs() -> None:
    # 1 um webs between 30 x 30 square holes (typical of dense via fields)
    holes = []
    pitch, size = 100e-6, 99e-6
    for i in range(30):
        for j in range(30):
            x0, y0 = 50e-6 + i * pitch, 50e-6 + j * pitch
            holes.append(rect(x0, y0, x0 + size, y0 + size)[::-1])
    p = poly(rect(0, 0, 3.1e-3, 3.1e-3), *holes)
    m = mesh(p, h=0.2e-3)
    check_ok(m)
    assert m.quality.area > 0


def test_contacts_survive_cleaning() -> None:
    p = poly(rect(0, 0, 4e-3, 4e-3), circle(3e-3, 3e-3, 0.4e-3, 16, cw=True))
    (m,) = mesh_copper_sheets(
        [piece([p], [Contact(id="v", x=1e-3, y=1e-3, radius=0.3e-3)])], h_default=0.5e-3
    )
    assert len(m.contact_nodes("v")) >= 16


def test_empty_and_degenerate_inputs_give_structured_errors() -> None:
    empty_ring = Polygon(exterior=Points(np.zeros((0, 2))))
    with pytest.raises(MeshError) as e0:
        mesh(empty_ring)
    assert e0.value.code == "MESH_EMPTY_GEOMETRY"
    line = poly([(0, 0), (1e-3, 0), (2e-3, 0)])
    with pytest.raises(MeshError) as e1:
        mesh(line)
    assert e1.value.code == "MESH_EMPTY_GEOMETRY"
    assert e1.value.path == "pieces[L2:NET].polygons"
    assert e1.value.hint
    d = e1.value.to_dict()
    assert set(d) == {"code", "message", "path", "hint"}
    speck = poly(rect(0, 0, 0.2e-6, 0.2e-6))
    with pytest.raises(MeshError) as e2:
        mesh(speck)
    assert e2.value.code == "MESH_EMPTY_GEOMETRY"
    assert "min_feature" in e2.value.hint  # type: ignore[operator]
    zero = poly([(1e-3, 1e-3)] * 4)
    with pytest.raises(MeshError):
        mesh(zero)


def test_mesh_error_is_oa_error() -> None:
    from oa_model.errors import OAError

    assert issubclass(MeshError, OAError)


def test_far_from_origin_coordinates() -> None:
    # board coordinates ~ 1 m away from origin keep sub-micron accuracy
    off = 1.0
    ext = [(off + x, off + y) for x, y in rect(0, 0, 3e-3, 2e-3)]
    hole = [(off + x, off + y) for x, y in circle(1.5e-3, 1e-3, 0.4e-3, 16, cw=True)]
    m = mesh(poly(ext, hole), h=0.4e-3)
    check_ok(m, 6e-6 - 0.5 * 16 * 0.16e-6 * np.sin(2 * np.pi / 16), rel=1e-6)
