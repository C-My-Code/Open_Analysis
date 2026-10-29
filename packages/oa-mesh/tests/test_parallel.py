# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""WP-211: hole-contact rings, process pool determinism, interior coarsening, algorithms."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from mesh_helpers import circle, piece, poly, random_centres, rect

from oa_mesh import Contact, MeshError, SizeField, mesh_copper_sheets


def test_hole_contact_ring_nodes_are_exact_and_distinct() -> None:
    """CR WP-204-mesh-hole-contacts: hole rings used to map to a few wrong nodes."""
    p = poly(rect(0, 0, 40e-3, 30e-3))
    cs = [
        Contact(id="a", x=0.0218, y=0.01325, radius=1e-4, segments=16, mode="hole"),
        Contact(id="b", x=0.0100, y=0.00500, radius=3e-4, mode="hole"),
        Contact(id="c", x=0.0300, y=0.02000, radius=2e-4, segments=24, mode="embedded"),
    ]
    (m,) = mesh_copper_sheets(
        [piece([p], cs)],
        h_default=2e-3,
        size_fields=[SizeField(kind="contacts", size=0.1e-3)],
    )
    for c, k in zip(cs, m.contacts, strict=True):
        idx = k.nodes.array
        assert len(set(idx.tolist())) == len(idx) >= 16
        r = np.hypot(m.nodes.array[idx, 0] - c.x, m.nodes.array[idx, 1] - c.y)
        assert np.abs(r - c.radius).max() <= 1e-9
        assert k.radial_error <= 1e-9


def _layers(n: int) -> list:  # type: ignore[type-arg]
    out = []
    for li in range(n):
        cs = random_centres(14, 16e-3, 12e-3, 1.5e-3, 1.8e-3, seed=20 + li)
        holes = [circle(x, y, 0.3e-3, 20, cw=True) for x, y in cs[:7]]
        con = [Contact(id=f"v{i}", x=x, y=y, radius=0.25e-3) for i, (x, y) in enumerate(cs[7:])]
        out.append(piece([poly(rect(0, 0, 16e-3, 12e-3), *holes)], con, pid=f"L{li}:N"))
    return out


def test_workers_do_not_change_the_mesh() -> None:
    pieces = _layers(5)
    sf = [SizeField(kind="contacts", size=0.1e-3)]
    serial = mesh_copper_sheets(pieces, h_default=1e-3, size_fields=sf, workers=1)
    two = mesh_copper_sheets(pieces, h_default=1e-3, size_fields=sf, workers=2)
    three = mesh_copper_sheets(pieces, h_default=1e-3, size_fields=sf, workers=3)
    ref = [m.content_hash() for m in serial]
    assert [m.content_hash() for m in two] == ref
    assert [m.content_hash() for m in three] == ref
    assert [m.piece_id for m in three] == [p.id for p in pieces]
    assert two[2].contacts == serial[2].contacts


def test_worker_errors_are_mesh_errors_and_pool_survives_unguarded_main(tmp_path: Path) -> None:
    """The pool never re-imports ``__main__``: a script without a guard works (spawn would not)."""
    script = tmp_path / "unguarded.py"
    script.write_text(
        "from oa_mesh import SheetPiece, mesh_copper_sheets\n"
        "from oa_model.arrays import Points\n"
        "from oa_model.geometry import Polygon\n"
        "def pc(i):\n"
        "    p = Polygon(exterior=Points([(0,0),(4e-3,0),(4e-3,3e-3),(0,3e-3)]))\n"
        "    return SheetPiece(id=f'p{i}', polygons=(p,))\n"
        "out = mesh_copper_sheets([pc(0), pc(1), pc(2)], h_default=1e-3, workers=2)\n"
        "print(len({m.content_hash() for m in out}), len(out))\n",
        encoding="utf-8",
    )
    r = subprocess.run(
        [sys.executable, "-I", str(script)], capture_output=True, text=True, timeout=120
    )
    assert r.returncode == 0, r.stderr[-500:]
    assert r.stdout.split() == ["1", "3"]


def test_workers_env_default_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    p = poly(rect(0, 0, 2e-3, 2e-3))
    with pytest.raises(MeshError) as ei:
        mesh_copper_sheets([piece([p])], h_default=1e-3, workers=0)
    assert ei.value.code == "MESH_SETUP"
    monkeypatch.setenv("OA_MESH_WORKERS", "x")
    with pytest.raises(MeshError):
        mesh_copper_sheets([piece([p])], h_default=1e-3)
    monkeypatch.setenv("OA_MESH_WORKERS", "2")
    out = mesh_copper_sheets([piece([p], pid="a"), piece([p], pid="b")], h_default=1e-3)
    assert [m.piece_id for m in out] == ["a", "b"]


def test_interior_coarsening_reduces_cells_and_keeps_area() -> None:
    p = poly(rect(0, 0, 80e-3, 60e-3))
    c = Contact(id="v", x=40e-3, y=30e-3, radius=0.3e-3)
    sf = [SizeField(kind="contacts", size=0.1e-3)]
    (a,) = mesh_copper_sheets([piece([p], [c])], h_default=1e-3, size_fields=sf)
    (b,) = mesh_copper_sheets([piece([p], [c])], h_default=1e-3, size_fields=sf, h_far=6e-3)
    assert b.quality.area == pytest.approx(a.quality.area, rel=1e-9)
    assert b.quality.n_triangles < 0.5 * a.quality.n_triangles
    assert b.quality.min_angle_deg > 20
    assert b.quality.max_edge > 3e-3  # the interior really is coarser than h_default
    r = b.contacts[0]
    assert r.radial_error <= 1e-9
    with pytest.raises(MeshError):
        mesh_copper_sheets([piece([p])], h_default=1e-3, h_far=0.5e-3)


@pytest.mark.parametrize("algorithm", ["delaunay", "meshadapt", "frontal_delaunay"])
def test_algorithms_produce_valid_meshes(algorithm: str) -> None:
    p = poly(rect(0, 0, 10e-3, 8e-3), circle(5e-3, 4e-3, 1e-3, 24, cw=True))
    (m,) = mesh_copper_sheets(
        [piece([p], [Contact(id="v", x=2e-3, y=2e-3, radius=0.3e-3)])],
        h_default=1e-3,
        algorithm=algorithm,  # type: ignore[arg-type]
    )
    assert m.quality.area == pytest.approx(
        80e-6 - 0.5 * 24 * 1e-6 * np.sin(2 * np.pi / 24), rel=1e-9
    )
    assert m.quality.min_angle_deg > 15
    assert m.contacts[0].radial_error <= 1e-9
    with pytest.raises(MeshError):
        mesh_copper_sheets([piece([p])], h_default=1e-3, algorithm="nope")  # type: ignore[arg-type]


def test_gmsh_threads_are_opt_in_and_valid() -> None:
    p = poly(rect(0, 0, 6e-3, 6e-3))
    (a,) = mesh_copper_sheets([piece([p])], h_default=0.5e-3)
    (b,) = mesh_copper_sheets([piece([p])], h_default=0.5e-3, threads=2)
    assert b.quality.area == pytest.approx(a.quality.area, rel=1e-12)
    with pytest.raises(MeshError):
        mesh_copper_sheets([piece([p])], h_default=0.5e-3, threads=0)
