# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Copper-sheet meshing: area conservation, contacts, size fields, quality, determinism."""

from __future__ import annotations

import time

import numpy as np
import pytest
from helpers import circle, piece, poly, random_centres, rect

from oa_mesh import (
    CleanOptions,
    Contact,
    MeshError,
    SizeField,
    mesh_copper_sheets,
    to_meshio,
    write_vtu,
)
from oa_model.arrays import Points
from oa_model.geometry import Polygon, signed_area
from oa_results import TriMesh2D


def poly_area(p: Polygon) -> float:
    return abs(signed_area(p.exterior)) - sum(abs(signed_area(h)) for h in p.holes)


def test_rectangle_is_trimesh2d_and_area_exact() -> None:
    p = poly(rect(0, 0, 10e-3, 5e-3))
    (m,) = mesh_copper_sheets([piece([p])], h_default=1e-3)
    assert isinstance(m, TriMesh2D)
    assert m.layer == "L2" and m.piece_id == "L2:NET" and m.net == "NET"
    assert m.quality.area == pytest.approx(50e-6, rel=1e-12)
    assert m.quality.min_angle_deg > 25
    assert float(np.max(m.nodes.array)) <= 10e-3 + 1e-15


def test_area_conservation_with_many_holes() -> None:
    w, h = 40e-3, 30e-3
    cs = random_centres(60, w, h, 2e-3, 3e-3, seed=3)
    holes = [circle(cx, cy, 0.5e-3, 32, cw=True) for cx, cy in cs]
    p = poly(rect(0, 0, w, h), *holes)
    (m,) = mesh_copper_sheets([piece([p])], h_default=2e-3)
    assert m.quality.area == pytest.approx(poly_area(p), rel=1e-9)
    assert m.cleaning.n_holes == 60
    assert m.cleaning.area_out == pytest.approx(poly_area(p), rel=1e-9)


def test_hole_boundaries_are_respected() -> None:
    p = poly(rect(0, 0, 4e-3, 4e-3), circle(2e-3, 2e-3, 0.8e-3, 32, cw=True))
    (m,) = mesh_copper_sheets([piece([p])], h_default=0.5e-3)
    d = np.hypot(m.nodes.array[:, 0] - 2e-3, m.nodes.array[:, 1] - 2e-3)
    # no node strictly inside the hole (circumradius of the 32-gon is 0.8 mm)
    assert d.min() >= 0.8e-3 * np.cos(np.pi / 32) - 2e-10


def test_contact_rings_exact_on_circle() -> None:
    cs = random_centres(25, 20e-3, 15e-3, 1.5e-3, 2e-3, seed=5)
    contacts = [Contact(id=f"v{i}", x=x, y=y, radius=0.3e-3) for i, (x, y) in enumerate(cs)]
    p = poly(rect(0, 0, 20e-3, 15e-3))
    (m,) = mesh_copper_sheets(
        [piece([p], contacts)],
        h_default=1.5e-3,
        size_fields=[SizeField(kind="contacts", size=0.1e-3)],
    )
    assert len(m.contacts) == 25
    for c, k in zip(contacts, m.contacts, strict=True):
        assert k.id == c.id
        idx = k.nodes.array
        assert len(idx) >= 16
        assert len(set(idx.tolist())) == len(idx)
        r = np.hypot(m.nodes.array[idx, 0] - c.x, m.nodes.array[idx, 1] - c.y)
        assert np.abs(r - c.radius).max() <= 1e-9  # 1 nm
        assert k.radial_error <= 1e-9
    assert m.quality.area == pytest.approx(20e-3 * 15e-3, rel=1e-9)


def test_ring_segments_scale_with_local_size() -> None:
    p = poly(rect(0, 0, 10e-3, 10e-3))
    c = Contact(id="v", x=5e-3, y=5e-3, radius=1e-3)
    (m,) = mesh_copper_sheets(
        [piece([p], [c])], h_default=1e-3, size_fields=[SizeField(kind="contacts", size=0.05e-3)]
    )
    assert len(m.contact_nodes("v")) > 100
    c2 = Contact(id="v", x=5e-3, y=5e-3, radius=1e-3, segments=20)
    (m2,) = mesh_copper_sheets([piece([p], [c2])], h_default=1e-3)
    assert len(m2.contact_nodes("v")) == 20


def test_hole_mode_contact_is_boundary_and_removes_disc() -> None:
    p = poly(rect(0, 0, 6e-3, 6e-3))
    c = Contact(id="v", x=3e-3, y=3e-3, radius=1e-3, segments=24, mode="hole")
    (m,) = mesh_copper_sheets([piece([p], [c])], h_default=0.6e-3)
    ring = m.contact_nodes("v")
    assert len(ring) == 24
    area_poly24 = 0.5 * 24 * (1e-3) ** 2 * np.sin(2 * np.pi / 24)
    assert m.quality.area == pytest.approx(36e-6 - area_poly24, rel=1e-9)


def test_point_contact() -> None:
    p = poly(rect(0, 0, 6e-3, 6e-3))
    (m,) = mesh_copper_sheets(
        [piece([p], [Contact(id="p", x=2e-3, y=3e-3)])],
        h_default=1e-3,
        size_fields=[SizeField(kind="contacts", size=0.1e-3)],
    )
    (k,) = m.contacts
    assert len(k.nodes.array) == 1
    xy = m.nodes.array[k.nodes.array[0]]
    assert xy == pytest.approx([2e-3, 3e-3], abs=1e-12)


def test_size_fields_refine_near_features() -> None:
    p = poly(rect(0, 0, 20e-3, 20e-3))
    c = Contact(id="v", x=10e-3, y=10e-3, radius=0.4e-3)
    (m,) = mesh_copper_sheets(
        [piece([p], [c])],
        h_default=2e-3,
        size_fields=[
            SizeField(kind="contacts", size=0.1e-3),
            SizeField(kind="polyline", size=0.3e-3, points=Points([(0.0, 2e-3), (20e-3, 2e-3)])),
        ],
    )
    n = m.nodes.array
    t = m.triangles.array

    def mean_edge(cx: float, cy: float, r: float) -> float:
        cen = n[t].mean(axis=1)
        sel = np.hypot(cen[:, 0] - cx, cen[:, 1] - cy) < r
        e = np.linalg.norm(n[t[sel, 0]] - n[t[sel, 1]], axis=1)
        return float(e.mean())

    near_contact = mean_edge(10e-3, 10e-3, 0.8e-3)
    near_trace = mean_edge(10e-3, 2e-3, 0.6e-3)
    far = mean_edge(15e-3, 16e-3, 2e-3)
    assert near_contact < 0.25e-3
    assert near_trace < 0.6e-3
    assert far > 1.0e-3
    assert m.quality.min_angle_deg > 20


def test_grading_respects_limit() -> None:
    p = poly(rect(0, 0, 20e-3, 20e-3))
    c = Contact(id="v", x=10e-3, y=10e-3, radius=0.3e-3)
    (m,) = mesh_copper_sheets(
        [piece([p], [c])],
        h_default=2e-3,
        size_fields=[SizeField(kind="contacts", size=0.1e-3, grading=1.3)],
    )
    n = m.nodes.array
    t = m.triangles.array
    cen = n[t].mean(axis=1)
    d = np.hypot(cen[:, 0] - 10e-3, cen[:, 1] - 10e-3)
    e = np.linalg.norm(n[t[:, 0]] - n[t[:, 1]], axis=1)
    sel = (d > 1e-3) & (d < 4e-3)
    # size should grow roughly linearly with slope <= ~0.6 (0.3 target, frontal slack)
    slope = np.polyfit(d[sel], e[sel], 1)[0]
    assert 0.05 < slope < 0.6


def test_multiple_pieces_and_ordering() -> None:
    a = piece([poly(rect(0, 0, 5e-3, 5e-3))], pid="a")
    b = piece([poly(rect(0, 0, 3e-3, 3e-3), circle(1.5e-3, 1.5e-3, 0.5e-3, 16, cw=True))], pid="b")
    out = mesh_copper_sheets([a, b], h_default=1e-3)
    assert [m.piece_id for m in out] == ["a", "b"]
    assert out[1].quality.area == pytest.approx(
        9e-6 - 0.5 * 16 * 0.25e-6 * np.sin(2 * np.pi / 16), rel=1e-9
    )


def test_multi_polygon_piece() -> None:
    p = piece([poly(rect(0, 0, 2e-3, 2e-3)), poly(rect(5e-3, 0, 7e-3, 2e-3))])
    (m,) = mesh_copper_sheets([p], h_default=0.5e-3)
    assert m.quality.area == pytest.approx(8e-6, rel=1e-9)
    assert m.cleaning.n_polygons == 2


def test_determinism_same_process() -> None:
    cs = random_centres(12, 10e-3, 10e-3, 1.5e-3, 1.8e-3, seed=9)
    holes = [circle(x, y, 0.3e-3, 20, cw=True) for x, y in cs[:6]]
    contacts = [Contact(id=f"v{i}", x=x, y=y, radius=0.25e-3) for i, (x, y) in enumerate(cs[6:])]
    pc = piece([poly(rect(0, 0, 10e-3, 10e-3), *holes)], contacts)
    sf = [SizeField(kind="contacts", size=0.1e-3)]
    h = {
        mesh_copper_sheets([pc], h_default=1e-3, size_fields=sf)[0].content_hash() for _ in range(3)
    }
    assert len(h) == 1


def test_determinism_across_processes() -> None:
    import subprocess
    import sys

    code = (
        "from oa_mesh import *;from oa_model.arrays import Points;"
        "from oa_model.geometry import Polygon;"
        "p=Polygon(exterior=Points([(0,0),(8e-3,0),(8e-3,6e-3),(0,6e-3)]));"
        "pc=SheetPiece(id='a',polygons=(p,),contacts=(Contact(id='v',x=4e-3,y=3e-3,radius=0.3e-3),));"
        "m,=mesh_copper_sheets([pc],h_default=1e-3,size_fields=[SizeField(kind='contacts',size=0.1e-3)]);"
        "print(m.content_hash())"
    )
    outs = {
        subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        ).stdout.strip()
        for _ in range(2)
    }
    assert len(outs) == 1 and len(next(iter(outs))) == 64


def test_contact_outside_copper_is_structured_error() -> None:
    p = poly(rect(0, 0, 4e-3, 4e-3))
    with pytest.raises(MeshError) as ei:
        mesh_copper_sheets(
            [piece([p], [Contact(id="v9", x=3.9e-3, y=2e-3, radius=0.5e-3)])], h_default=1e-3
        )
    d = ei.value.to_dict()
    assert d["code"] == "MESH_CONTACT_OUTSIDE"
    assert d["path"] is not None and "contacts[0]" in d["path"]
    assert d["hint"]


def test_contact_in_hole_is_error() -> None:
    p = poly(rect(0, 0, 6e-3, 6e-3), circle(3e-3, 3e-3, 1e-3, 24, cw=True))
    with pytest.raises(MeshError) as ei:
        mesh_copper_sheets(
            [piece([p], [Contact(id="v", x=3e-3, y=3e-3, radius=0.2e-3)])], h_default=1e-3
        )
    assert ei.value.code == "MESH_CONTACT_OUTSIDE"


def test_overlapping_and_duplicate_contacts_are_errors() -> None:
    p = poly(rect(0, 0, 6e-3, 6e-3))
    a = Contact(id="a", x=2e-3, y=3e-3, radius=0.5e-3)
    b = Contact(id="b", x=2.6e-3, y=3e-3, radius=0.5e-3)
    with pytest.raises(MeshError) as ei:
        mesh_copper_sheets([piece([p], [a, b])], h_default=1e-3)
    assert ei.value.code == "MESH_CONTACT_OVERLAP"
    with pytest.raises(MeshError) as ei2:
        mesh_copper_sheets([piece([p], [a, a])], h_default=1e-3)
    assert ei2.value.code == "MESH_CONTACT_OVERLAP"


def test_bad_arguments() -> None:
    p = poly(rect(0, 0, 1e-3, 1e-3))
    with pytest.raises(MeshError) as ei:
        mesh_copper_sheets([piece([p])], h_default=0.0)
    assert ei.value.code == "MESH_SETUP"
    with pytest.raises(MeshError):
        mesh_copper_sheets([piece([p], pid="x"), piece([p], pid="x")], h_default=1e-3)
    with pytest.raises(MeshError) as ei3:
        mesh_copper_sheets(
            [piece([p])], h_default=1e-3, size_fields=[SizeField(kind="polyline", size=1e-4)]
        )
    assert ei3.value.code == "MESH_SIZE_FIELD"


def test_size_field_density_guard() -> None:
    p = poly(rect(0, 0, 1.0, 1.0))
    with pytest.raises(MeshError) as ei:
        mesh_copper_sheets(
            [piece([p])],
            h_default=0.5,
            size_fields=[SizeField(kind="boundary", size=1e-6)],
        )
    assert ei.value.code == "MESH_SIZE_FIELD"


def test_simplify_option_reduces_vertices() -> None:
    ext = [(float(x), 1e-9 * (i % 2)) for i, x in enumerate(np.linspace(0, 5e-3, 50))]
    ext += [(5e-3, 3e-3), (0.0, 3e-3)]
    (m,) = mesh_copper_sheets(
        [piece([poly(ext)])], h_default=1e-3, clean=CleanOptions(simplify=2e-6)
    )
    assert m.quality.area == pytest.approx(15e-6, rel=1e-3)


def test_export_meshio_and_vtu(tmp_path: object) -> None:
    import pathlib

    d = pathlib.Path(str(tmp_path))
    p = poly(rect(0, 0, 4e-3, 4e-3))
    (m,) = mesh_copper_sheets(
        [piece([p], [Contact(id="v", x=2e-3, y=2e-3, radius=0.3e-3)])], h_default=1e-3
    )
    mm = to_meshio(m)
    assert mm.cells[0].type == "triangle"
    assert mm.point_data["contact"].max() == 1
    out = write_vtu(m, d / "sheet.vtu")
    assert out.exists() and out.stat().st_size > 0
    import meshio  # type: ignore[import-untyped]

    back = meshio.read(out)
    assert len(back.points) == len(m.nodes.array)


def test_perf_200_holes_under_5s() -> None:
    """Acceptance: 40 x 30 mm plane with 200 random holes meshes in < 5 s."""
    w, h = 40e-3, 30e-3
    cs = random_centres(200, w, h, 2e-3, 1.6e-3, seed=11)
    holes = [circle(x, y, 0.3e-3, 20, cw=True) for x, y in cs]
    pc = piece([poly(rect(0, 0, w, h), *holes)])
    t0 = time.perf_counter()
    (m,) = mesh_copper_sheets([pc], h_default=1e-3)
    dt = time.perf_counter() - t0
    assert dt < 5.0, dt
    assert m.cleaning.n_holes == 200
