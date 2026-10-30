# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cross-section meshing on every WP-000 CrossSection fixture."""

from __future__ import annotations

import math
import pathlib
import platform

import numpy as np
import pytest

from oa_mesh import MeshError, XsMesh, mesh_cross_section, to_meshio, write_vtu
from oa_mesh._quality import quality_report
from oa_model.arrays import Points
from oa_model.geometry import Polygon
from oa_model.materials import ConstantDkDf
from oa_model.xsection import (
    CrossSection,
    XsConductor,
    XsDielectric,
    XsPlane,
    XsPolygon,
    XsRegion,
    XsSlab,
    XsTrapezoid,
)
from oa_testkit.golden import assert_close_to_golden, load_case, update_mode
from oa_testkit.xsections import XsCase, all_cases, get_case

CASES = all_cases()
IDS = [c.name for c in CASES]


@pytest.fixture(scope="module", params=CASES, ids=IDS)
def meshed(request: pytest.FixtureRequest) -> tuple[XsCase, XsMesh]:
    case: XsCase = request.param
    return case, mesh_cross_section(case.xs)


def region_areas(m: XsMesh) -> dict[int, float]:
    n = m.nodes.array
    t = m.triangles.array[:, :3]
    a = 0.5 * np.abs(
        (n[t[:, 1], 0] - n[t[:, 0], 0]) * (n[t[:, 2], 1] - n[t[:, 0], 1])
        - (n[t[:, 2], 0] - n[t[:, 0], 0]) * (n[t[:, 1], 1] - n[t[:, 0], 1])
    )
    r = m.region.array
    return {int(k): float(a[r == k].sum()) for k in np.unique(r)}


def test_quality_and_tags_on_all_fixtures(meshed: tuple[XsCase, XsMesh]) -> None:
    case, m = meshed
    xs = case.xs
    assert m.order == 2
    assert m.triangles.array.shape[1] == 6
    # acceptance: no triangle below 15 degrees
    assert m.quality.min_angle_deg >= 15.0, m.quality
    # recomputing from raw arrays agrees with the report
    q = quality_report(m.nodes.array, m.triangles.array)
    assert q.min_angle_deg == pytest.approx(m.quality.min_angle_deg)
    # every conductor boundary is tagged, in conductor order
    assert [e.source_id for e in m.conductor_edges] == [c.id for c in xs.conductors]
    for e in m.conductor_edges:
        assert e.edges.array.shape[0] > 0, f"no boundary edges for {e.source_id}"
        assert e.edges.array.shape[1] == 3
    # a closed shield (coax) leaves nothing on the truncation box
    assert any(g.name == "outer" for g in m.groups) == (xs.topology_hint != "coax")
    # all triangles positively oriented, indices valid, no orphan nodes
    n = m.nodes.array
    t = m.triangles.array
    assert t.min() == 0 and t.max() == len(n) - 1
    assert len(np.unique(t)) == len(n)
    d1 = n[t[:, 1]] - n[t[:, 0]]
    d2 = n[t[:, 2]] - n[t[:, 0]]
    assert (d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0] > 0).all()
    # regions: dielectric indices exist; air is -1
    valid = {-1, *range(len(xs.dielectrics))}
    assert set(np.unique(m.region.array).tolist()) <= valid
    if xs.dielectrics:
        assert set(range(len(xs.dielectrics))) <= set(np.unique(m.region.array).tolist())


def test_p2_midnodes_are_edge_midpoints(meshed: tuple[XsCase, XsMesh]) -> None:
    _, m = meshed
    n = m.nodes.array
    t = m.triangles.array
    sel = slice(None, None, max(1, len(t) // 500))
    for i, (a, b, mid) in enumerate([(0, 1, 3), (1, 2, 4), (2, 0, 5)]):
        got = n[t[sel, mid]]
        want = 0.5 * (n[t[sel, a]] + n[t[sel, b]])
        assert np.abs(got - want).max() < 1e-9 * (m.box[2] - m.box[0]), i


def test_conductor_edge_nodes_lie_on_conductors() -> None:
    case = get_case("hj_microstrip_u2_er4p3")
    m = mesh_cross_section(case.xs, order=1)
    s1 = case.xs.conductors[0].shape
    assert isinstance(s1, XsTrapezoid)
    n = m.nodes.array[m.conductor_edge_set("s1").edges.array.ravel()]
    u0, u1 = s1.u_center - s1.w_bottom / 2, s1.u_center + s1.w_bottom / 2
    z0, z1 = s1.z_bottom, s1.z_bottom + s1.thickness
    on_u = (np.isclose(n[:, 0], u0, atol=1e-12) | np.isclose(n[:, 0], u1, atol=1e-12)) & (
        (n[:, 1] >= z0 - 1e-12) & (n[:, 1] <= z1 + 1e-12)
    )
    on_z = (np.isclose(n[:, 1], z0, atol=1e-12) | np.isclose(n[:, 1], z1, atol=1e-12)) & (
        (n[:, 0] >= u0 - 1e-12) & (n[:, 0] <= u1 + 1e-12)
    )
    assert (on_u | on_z).all()
    # the ground plane (zero-thickness at z = 0) is tagged along the whole bottom
    g = m.nodes.array[m.conductor_edge_set("g1").edges.array.ravel()]
    assert np.abs(g[:, 1]).max() < 1e-12
    assert g[:, 0].min() == pytest.approx(m.box[0]) and g[:, 0].max() == pytest.approx(m.box[2])
    # outer boundary: only the box sides and top (the ground side is the plane)
    o = m.nodes.array[m.outer_edges.edges.array.ravel()]
    assert o[:, 0].min() == pytest.approx(m.box[0])
    assert o[:, 1].max() == pytest.approx(m.box[3])


def test_microstrip_region_areas_exact() -> None:
    case = get_case("hj_microstrip_u2_er4p3")
    m = mesh_cross_section(case.xs)
    h = 2e-4
    width = m.box[2] - m.box[0]
    areas = region_areas(m)
    assert areas[0] == pytest.approx(width * h, rel=1e-9)  # dielectric slab
    strip = 4e-4 * 2e-8
    assert areas[-1] == pytest.approx(width * (m.box[3] - h) - strip, rel=1e-9)  # air above
    # the space below the ground plane was dropped
    assert any("unreachable" in w for w in m.warnings)
    assert m.quality.area == pytest.approx(areas[0] + areas[-1])


def test_coax_region_area_and_dropped_exterior() -> None:
    case = get_case("coax_lnba1_er2p1")
    m = mesh_cross_section(case.xs)
    a = 0.5e-3
    # equal-area 1024-gon ring of the dielectric minus the 128-gon inner conductor
    areas = region_areas(m)
    assert set(areas) == {0}  # no air: the shield encloses the dielectric
    poly_b = case.xs.dielectrics[0].region.polygon  # type: ignore[union-attr]
    xy = poly_b.exterior.array
    area_b = 0.5 * abs(np.sum(xy[:, 0] * np.roll(xy[:, 1], -1) - np.roll(xy[:, 0], -1) * xy[:, 1]))
    area_a = 0.5 * 128 * a**2 * math.sin(2 * math.pi / 128)
    assert areas[0] == pytest.approx(area_b - area_a, rel=1e-9)
    assert m.outer_edges.edges.array.shape[0] == 0


def test_p1_and_mesh_conductors() -> None:
    case = get_case("cpw_s1_g0p5_air")
    p1 = mesh_cross_section(case.xs, order=1)
    assert p1.order == 1 and p1.triangles.array.shape[1] == 3
    assert p1.conductor_edge_set("s1").edges.array.shape[1] == 2
    both = mesh_cross_section(case.xs, order=1, mesh_conductors=True)
    assert both.quality.n_triangles > p1.quality.n_triangles
    ra = region_areas(both)
    assert -2 in ra and -3 in ra and -4 in ra  # s1, g1, g2 interiors
    assert ra[-2] == pytest.approx(1e-3 * 1e-7, rel=1e-6)
    names = {g.name for g in both.groups}
    assert {"conductor_interior:s1", "conductor:g1", "air", "outer"} <= names


def test_h_max_and_corner_refinement() -> None:
    case = get_case("hj_microstrip_u2_er4p3")
    coarse = mesh_cross_section(case.xs, order=1, h_max=1e-4, corner_refine=0.2)
    fine = mesh_cross_section(case.xs, order=1, h_max=2.5e-5, corner_refine=0.05)
    assert fine.quality.n_triangles > coarse.quality.n_triangles
    assert fine.quality.max_edge <= 2.6e-5 * 1.5
    # refinement concentrates at the strip corners: smallest edge near the strip edge
    n = fine.nodes.array
    t = fine.triangles.array
    cen = n[t].mean(axis=1)
    e = np.linalg.norm(n[t[:, 0]] - n[t[:, 1]], axis=1)
    near = np.hypot(cen[:, 0] - 2e-4, cen[:, 1] - 2e-4) < 2e-5
    far = np.hypot(cen[:, 0], cen[:, 1] - 1e-4) > 2e-4
    assert e[near].mean() < 0.2 * e[far].mean()


def test_physical_groups_trace_to_ids(meshed: tuple[XsCase, XsMesh]) -> None:
    case, m = meshed
    ids = {c.id for c in case.xs.conductors}
    cg = {g.source_id: g for g in m.groups if g.kind == "conductor"}
    assert set(cg) == ids
    assert len({(g.dim, g.tag) for g in m.groups}) == len(m.groups)
    for i in range(len(case.xs.dielectrics)):
        assert any(g.kind == "dielectric" and g.source_id == str(i) for g in m.groups)


def test_two_signal_conductor_order_is_preserved() -> None:
    case = get_case("edge_coupled_stripline_w0p5_s0p25_er1")
    m = mesh_cross_section(case.xs, order=1)
    assert [e.source_id for e in m.conductor_edges] == ["g1", "s1", "s2", "g2"]


def test_determinism_hash_repeatable_and_multiprocess() -> None:
    import subprocess
    import sys

    case = get_case("cohn_stripline_w0p5_er1")
    h1 = mesh_cross_section(case.xs).content_hash()
    h2 = mesh_cross_section(case.xs).content_hash()
    assert h1 == h2
    code = (
        "from oa_testkit.xsections import get_case;from oa_mesh import mesh_cross_section;"
        "print(mesh_cross_section(get_case('cohn_stripline_w0p5_er1').xs).content_hash())"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == h1


@pytest.mark.golden
@pytest.mark.parametrize(
    "name",
    ["hj_microstrip_u2_er4p3", "cohn_stripline_w0p5_er1", "cpw_s1_g0p5_air", "coax_lnba1_er2p1"],
)
def test_golden_mesh_signature(name: str) -> None:
    """Node/triangle counts and coordinate hash for a fixed gmsh version (also Linux CI)."""
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        # The exact node/triangle counts and coordinate hash come from gmsh's floating-point path,
        # which differs on arm64 (macOS). Mesh validity there is covered by the area, quality and
        # determinism tests; the exact signature is checked on x86_64 (Windows and Linux CI).
        pytest.skip("exact gmsh signature is recorded for x86_64 only")
    m = mesh_cross_section(get_case(name).xs)
    if not update_mode():
        recorded = load_case("mesh2d", f"xs_{name}").expected["gmsh_version"]
        if recorded != m.gmsh_version:
            pytest.skip(f"golden recorded with gmsh {recorded}, running {m.gmsh_version}")
    assert_close_to_golden(
        {
            "gmsh_version": m.gmsh_version,
            "n_nodes": m.quality.n_nodes,
            "n_triangles": m.quality.n_triangles,
            "hash": m.content_hash(),
        },
        "mesh2d",
        f"xs_{name}",
        source=(
            "self-generated regression signature (gmsh 4.15.2, Windows); node/triangle counts "
            "and a "
            "coordinate hash rounded to 1e-12 x box size. Not an independent reference."
        ),
        inputs={"case": name, "order": 2},
    )


def test_lone_strip_in_air_meshes() -> None:
    xs = CrossSection(
        conductors=(
            XsConductor(
                id="s1",
                role="signal",
                shape=XsTrapezoid(
                    u_center=0, z_bottom=1e-4, w_bottom=1e-4, w_top=1e-4, thickness=1e-5
                ),
            ),
        ),
        dielectrics=(),
    )
    m = mesh_cross_section(xs, order=1)  # a lone strip in air still meshes
    assert m.quality.min_angle_deg > 15


def test_bad_arguments() -> None:
    xs = get_case("hj_microstrip_u2_er4p3").xs
    for kw in (
        {"order": 3},
        {"grading": 1.0},
        {"corner_refine": 0.0},
        {"box_factor": -1.0},
        {"h_max": -1.0},
    ):
        with pytest.raises(MeshError) as ei:
            mesh_cross_section(xs, **kw)
        assert ei.value.code == "MESH_SETUP"
        assert ei.value.path


def test_polygon_conductor_with_dielectric_region_priority() -> None:
    """A conformal region (priority 10) overrides the slab where they overlap."""
    ring = [(-3e-4, 2e-4), (3e-4, 2e-4), (3e-4, 4e-4), (-3e-4, 4e-4)]
    xs = CrossSection(
        conductors=(
            XsConductor(
                id="s1",
                role="signal",
                shape=XsPolygon(
                    polygon=Polygon(
                        exterior=Points(
                            [(-1e-4, 2e-4), (1e-4, 2e-4), (1e-4, 2.4e-4), (-1e-4, 2.4e-4)]
                        )
                    )
                ),
            ),
            XsConductor(id="g1", role="ground", shape=XsPlane(z_bottom=0.0, thickness=0.0)),
        ),
        dielectrics=(
            XsDielectric(
                region=XsSlab(z_bottom=0.0, z_top=2e-4), model=ConstantDkDf(dk=4.0, df=0.0)
            ),
            XsDielectric(
                region=XsRegion(polygon=Polygon(exterior=Points(ring))),
                model=ConstantDkDf(dk=3.0, df=0.0),
                priority=10,
            ),
        ),
    )
    m = mesh_cross_section(xs, order=1)
    areas = region_areas(m)
    assert areas[1] == pytest.approx(6e-4 * 2e-4 - 2e-4 * 4e-5, rel=1e-9)
    assert m.quality.min_angle_deg > 15


def test_export_vtu(tmp_path: pathlib.Path) -> None:
    m = mesh_cross_section(get_case("hj_microstrip_u2_er4p3").xs)
    mm = to_meshio(m)
    assert mm.cells[0].type == "triangle6"
    assert set(mm.point_data) == {"conductor", "outer"}
    out = write_vtu(m, tmp_path / "xs.vtu")
    import meshio  # type: ignore[import-untyped]

    back = meshio.read(out)
    assert back.cells[0].type == "triangle6"
    assert len(back.points) == len(m.nodes.array)
