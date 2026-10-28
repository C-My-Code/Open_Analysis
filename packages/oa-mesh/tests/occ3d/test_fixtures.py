# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""WP-206 acceptance: EMProblem fixtures and the cut_region via region mesh consistently."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from occ3d_helpers import (
    assert_groups_complete,
    assert_watertight,
    read_msh,
    tet_volume,
    thick_board,
)

from oa_geometry import BoardGeometry, RegionSpec
from oa_mesh.occ3d import OccModel, OccOptions, mesh_problem
from oa_mesh.occ3d.mesher import build_occ_model, write_msh
from oa_model.em import EMProblem, MeshSettings
from oa_testkit.builders import via_transition_board
from oa_testkit.emproblems import (
    microstrip_thru_problem,
    stripline_thru_problem,
    via_transition_problem,
)

PROBLEMS = {
    "microstrip": microstrip_thru_problem,
    "stripline": stripline_thru_problem,
    "via": via_transition_problem,
}


def _mesh(problem: EMProblem, tmp_path: Path, **kw: object) -> tuple[OccModel, object]:
    return mesh_problem(problem, tmp_path / "p.msh", **kw)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["microstrip", "stripline"])
def test_simple_fixtures_exact_volumes(name: str, tmp_path: Path) -> None:
    """Planar geometry: material volumes equal slab volume minus conductors to 1e-9."""
    p = PROBLEMS[name]()
    model, stats = _mesh(p, tmp_path, sheet_threshold=0.0)  # thin copper as solids
    rep = model.report
    assert rep is not None
    st = p.structure
    box = st.bbox
    area = (box.xmax - box.xmin) * (box.ymax - box.ymin)
    box_v = area * (box.zmax - box.zmin)
    assert rep.box_volume == pytest.approx(box_v, rel=1e-9)
    cu = 0.0
    for c in st.conductors:
        a = sum(
            _poly_area(q.exterior.array) - sum(_poly_area(h.array) for h in q.holes)
            for q in c.polygons
        )
        cu += a * (c.z_top - c.z_bottom)
    assert rep.copper_volume == pytest.approx(cu, rel=1e-6)
    assert rep.domain_volume == pytest.approx(box_v - cu, rel=1e-9)
    fr4 = sum(s.z_top - s.z_bottom for s in st.slabs) * area
    # resin between traces fills the inner copper layer: counted with the dielectric
    inner = [c for c in st.conductors if _inner(c, st)]
    fr4 += sum((c.z_top - c.z_bottom) * area for c in inner)
    fr4 -= sum(_volume(c) for c in inner)
    assert rep.group("mat:FR4_generic").measure == pytest.approx(fr4, rel=1e-6)
    # tetrahedra fill the model exactly (all faces are planar)
    assert stats.tet_volume == pytest.approx(rep.domain_volume, rel=1e-9)  # type: ignore[attr-defined]


def _poly_area(ring: np.ndarray) -> float:
    x, y = ring[:, 0], ring[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0)


def _volume(c: object) -> float:
    return sum(
        (_poly_area(q.exterior.array) - sum(_poly_area(h.array) for h in q.holes))
        * (c.z_top - c.z_bottom)  # type: ignore[attr-defined]
        for q in c.polygons  # type: ignore[attr-defined]
    )


def _inner(c: object, st: object) -> bool:
    zb, zt = c.z_bottom, c.z_top  # type: ignore[attr-defined]
    slabs = st.slabs  # type: ignore[attr-defined]
    return any(abs(s.z_top - zb) < 1e-12 for s in slabs) and any(
        abs(s.z_bottom - zt) < 1e-12 for s in slabs
    )


@pytest.mark.parametrize("name", ["microstrip", "stripline", "via"])
def test_fixture_groups_ports_and_mesh(name: str, tmp_path: Path) -> None:
    p = PROBLEMS[name]()
    model, stats = _mesh(p, tmp_path)
    rep = model.report
    assert rep is not None
    names = {g.name for g in rep.groups}
    # material, conductor and port groups exist with unique tags
    assert any(n.startswith("mat:") for n in names)
    assert {f"port:{pt.name}" for pt in p.ports} <= names
    for c in p.structure.conductors:
        assert f"cond:{c.id}" in names
    for v in p.structure.vias:
        assert f"via:{v.id}" in names
    assert len({g.tag for g in rep.groups}) == len(rep.groups)
    # every port is a coplanar face set whose area is the port rectangle minus copper inside it
    for pt in p.ports:
        g = rep.group(f"port:{pt.name}")
        assert g.dim == 2 and g.kind == "port" and g.model_id == pt.name
    data = read_msh(Path(stats.path))  # type: ignore[attr-defined]
    assert_watertight(data.tets)
    assert_groups_complete(data, stats)  # type: ignore[arg-type]
    assert stats.min_sicn > 0.1  # type: ignore[attr-defined]
    assert stats.dof_p2 > stats.dof_p1 > stats.n_tets // 2  # type: ignore[attr-defined]
    side = json.loads(Path(stats.path).with_suffix(".groups.json").read_text())  # type: ignore[attr-defined]
    assert {g["name"] for g in side["groups"]} == names
    assert side["boundary_bc"]["zmin"] == p.boundaries.zmin


def test_lumped_port_is_one_surface(tmp_path: Path) -> None:
    p = microstrip_thru_problem()
    model, _ = _mesh(p, tmp_path)
    rep = model.report
    assert rep is not None
    for pt in ("P1", "P2"):
        g = rep.group(f"port:{pt}")
        assert g.n_entities == 1
        w = p.ports[0].width  # type: ignore[union-attr]
        h = 0.2e-3 - 0.0  # dielectric height between the plane and the trace
        assert g.measure == pytest.approx(w * h, rel=1e-6)


def test_wave_port_tiles_the_face(tmp_path: Path) -> None:
    """Wave port faces tile the port rectangle minus the copper cross-section, all coplanar."""
    p = stripline_thru_problem()
    model, stats = _mesh(p, tmp_path, sheet_threshold=0.0)
    rep = model.report
    assert rep is not None
    data = read_msh(Path(stats.path))  # type: ignore[attr-defined]
    for pt in p.ports:
        g = rep.group(f"port:{pt.name}")
        u0, u1, z0, z1 = pt.rect  # type: ignore[union-attr]
        trace = next(c for c in p.structure.conductors if c.id in pt.signal_conductors)  # type: ignore[union-attr]
        rect_area = (u1 - u0) * (z1 - z0)
        t = trace.z_top - trace.z_bottom
        copper = 0.2e-3 * t + 2 * (u1 - u0) * t  # trace width x t, plus the two planes
        assert g.measure == pytest.approx(rect_area - copper, rel=1e-6)
        tris = data.tris[data.tri_tag == g.tag]
        xs = data.points[tris.ravel(), 0]
        assert np.ptp(xs) < 1e-12  # one plane


def test_deterministic_remesh(tmp_path: Path) -> None:
    p = microstrip_thru_problem()
    _, s1 = _mesh(p, tmp_path / "a")
    _, s2 = _mesh(p, tmp_path / "b")
    assert s1.n_tets == s2.n_tets and s1.n_nodes == s2.n_nodes  # type: ignore[attr-defined]
    assert s1.content_hash == s2.content_hash  # type: ignore[attr-defined]
    assert (tmp_path / "a" / "p.msh").read_bytes() == (tmp_path / "b" / "p.msh").read_bytes()


def test_size_field_follows_wavelength(tmp_path: Path) -> None:
    st = thick_board()
    model = build_occ_model(st, sheet_threshold=50e-6, options=OccOptions(f_max=30e9))
    lo = write_msh(model, tmp_path / "lo.msh", sizes=MeshSettings(preset="coarse"))
    hi = write_msh(model, tmp_path / "hi.msh", sizes=MeshSettings(preset="medium"))
    # air: lambda/N exactly; FR4 (eps_r > 4) is finer than air
    assert hi.h_max == pytest.approx(299792458 / 30e9 / 10, rel=1e-3)
    assert lo.h_max == pytest.approx(299792458 / 30e9 / 6, rel=1e-3)
    assert hi.h_min < hi.h_max / 2


@pytest.fixture(scope="module")
def via_cut():  # type: ignore[no-untyped-def]
    g = BoardGeometry.build(via_transition_board())
    return g.cut_region(RegionSpec(nets=("SIG",), bbox=(5e-3, -4e-3, 20e-3, 4e-3)))


def test_cut_region_via_meshes(via_cut, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    st = via_cut.structure
    model = build_occ_model(st, sheet_threshold=0.5 * 0.2e-3, options=OccOptions(f_max=10e9))
    rep = model.report
    assert rep is not None
    assert any(g.name.startswith("via:") for g in rep.groups)
    stats = write_msh(model, tmp_path / "cut.msh", sizes=MeshSettings())
    data = read_msh(Path(stats.path))
    assert_watertight(data.tets)
    assert_groups_complete(data, stats)
    assert tet_volume(data.points, data.tets) == pytest.approx(stats.tet_volume, rel=1e-9)
