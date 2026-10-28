# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Robustness suite: thin copper, coincident faces, via through antipads, backdrilled stub."""

from __future__ import annotations

import math
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from occ3d_helpers import (
    assert_groups_complete,
    assert_watertight,
    fr4_materials,
    read_msh,
    rect_poly,
    thick_board,
    with_vias,
)

from oa_mesh import MeshError
from oa_mesh.occ3d import OccOptions, build_occ_model, export_glb, write_msh
from oa_model.em import Box3, ConductorBody, MaterialSlab, Structure3D
from oa_model.materials import ConductorMaterial
from oa_testkit.emproblems import via_transition_problem


def _check(model, tmp_path: Path, **kw):  # type: ignore[no-untyped-def]
    stats = write_msh(model, tmp_path / "r.msh", **kw)
    data = read_msh(Path(stats.path))
    assert_watertight(data.tets)
    assert_groups_complete(data, stats)
    assert stats.min_sicn > 0.05
    return stats, data


def test_types_import_does_not_load_gmsh() -> None:
    code = "import sys, oa_mesh.occ3d; assert 'gmsh' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.parametrize("threshold", [0.0, 50e-6], ids=["solid", "sheet"])
def test_thin_copper_in_thick_board(threshold: float, tmp_path: Path) -> None:
    """18 um copper in a 1.6 mm board, as solids and as sheets."""
    st = thick_board()
    model = build_occ_model(st, sheet_threshold=threshold, options=OccOptions(f_max=2e9))
    rep = model.report
    assert rep is not None
    lx, ly = 4e-3, 3e-3
    floor = 18e-6 if threshold else 0.0  # sheet mode clips the domain at the bottom sheet
    assert rep.box_volume == pytest.approx(lx * ly * (st.bbox.zmax - floor), rel=1e-9)
    cu = 18e-6 * (4 * lx * ly - lx * ly + lx * 0.3e-3)  # 3 full planes + L2 plane... see below
    if threshold == 0.0:
        planes = 3 * lx * ly * 18e-6
        trace = lx * 0.3e-3 * 18e-6
        cu = planes + trace
        assert rep.copper_volume == pytest.approx(cu, rel=1e-6)
    else:
        assert rep.copper_volume == 0.0
        assert all(g.representation == "sheet" for g in rep.groups if g.kind == "conductor")
    assert rep.domain_volume + rep.copper_volume < rep.box_volume * (1 + 1e-9)
    _check(model, tmp_path)


def test_coincident_faces_and_touching_bodies(tmp_path: Path) -> None:
    """Bodies sharing an edge, duplicated copper, slab/conductor faces on the box wall, a
    sub-micron sliver: none of it may break the boolean."""
    st = thick_board()
    t = st.conductors[0]
    z0, z1 = t.z_bottom, t.z_top
    extra = (
        # touches the trace along y = +w/2 (shared edge)
        ConductorBody(
            id="L1:T2",
            net="SIG",
            role="signal",
            layer="L1",
            z_bottom=z0,
            z_top=z1,
            polygons=(rect_poly(0.0, 0.15e-3, 4e-3, 0.45e-3),),
        ),
        # exact duplicate of the trace
        ConductorBody(
            id="L1:DUP",
            net="SIG",
            role="signal",
            layer="L1",
            z_bottom=z0,
            z_top=z1,
            polygons=t.polygons,
        ),
        # 0.4 um wide sliver glued to the trace
        ConductorBody(
            id="L1:SLIVER",
            net="SIG",
            role="signal",
            layer="L1",
            z_bottom=z0,
            z_top=z1,
            polygons=(rect_poly(0.0, -0.6e-3, 4e-3, -0.6e-3 + 0.4e-6),),
        ),
    )
    st2 = st.model_copy(update={"conductors": (*st.conductors, *extra)})
    model = build_occ_model(st2, sheet_threshold=0.0, options=OccOptions(f_max=2e9))
    rep = model.report
    assert rep is not None
    assert not rep.warnings or all("dropped" in w or "no surface" in w for w in rep.warnings)
    _check(model, tmp_path)


def test_unknown_material_is_a_structured_error() -> None:
    st = thick_board()
    bad = st.model_copy(
        update={"slabs": (MaterialSlab(material="nope", z_bottom=0.0, z_top=1e-3),)}
    )
    with pytest.raises(MeshError) as ei:
        build_occ_model(bad, sheet_threshold=0.0)
    assert ei.value.code == "MESH_SETUP"


def _via_geometry_checks(model, via_ids) -> None:  # type: ignore[no-untyped-def]
    rep = model.report
    assert rep is not None
    for vid in via_ids:
        g = rep.group(f"via:{vid}")
        assert g.n_entities >= 1 and g.measure > 0


def test_via_through_planes_with_antipads_sheet_volumes(tmp_path: Path) -> None:
    """Sheet mode: the only removed copper is the barrels; FR4 matches the analytic value."""
    p = via_transition_problem()
    st = p.structure
    model = build_occ_model(st, sheet_threshold=1e-4, ports=p.ports, options=OccOptions(f_max=20e9))
    rep = model.report
    assert rep is not None
    _via_geometry_checks(model, [v.id for v in st.vias])
    v = st.vias[0]
    tube = math.pi * (v.r_outer**2 - (v.r_outer - v.plating) ** 2)
    box = st.bbox
    area = (box.xmax - box.xmin) * (box.ymax - box.ymin)
    zf0, zf1 = st.slabs[2].z_bottom, st.slabs[0].z_top  # dielectric span (sheets on faces)
    zf0 = min(s.z_bottom for s in st.slabs)
    assert rep.copper_volume == pytest.approx(
        len(st.vias) * tube * (v.z_top - v.z_bottom), rel=1e-6
    )
    fr4 = area * (zf1 - zf0) - len(st.vias) * tube * (zf1 - zf0)
    assert rep.group("mat:FR4_generic").measure == pytest.approx(fr4, rel=1e-6)
    _check(model, tmp_path)


def test_via_through_planes_solid(tmp_path: Path) -> None:
    p = via_transition_problem()
    model = build_occ_model(
        p.structure, sheet_threshold=0.0, ports=p.ports, options=OccOptions(f_max=20e9)
    )
    rep = model.report
    assert rep is not None
    assert rep.domain_volume + rep.copper_volume == pytest.approx(rep.box_volume, rel=1e-9)
    _check(model, tmp_path)


@pytest.mark.parametrize("void", [None, 0.1e-3], ids=["dielectric", "void"])
def test_backdrilled_stub(void: float | None, tmp_path: Path) -> None:
    """Signal via plated only L1..L3 (stub below removed); barrel ends inside the dielectric."""
    p = via_transition_problem()
    st = p.structure
    sig = next(v for v in st.vias if v.net == "SIG")
    cut = sig.model_copy(update={"z_bottom": 0.2e-3})
    vias = tuple(cut if v.id == sig.id else v for v in st.vias)
    st2 = with_vias(st, vias)
    model = build_occ_model(
        st2,
        sheet_threshold=1e-4,
        ports=p.ports,
        options=OccOptions(f_max=10e9, backdrill_clearance=void),
    )
    rep = model.report
    assert rep is not None
    assert rep.group(f"via:{sig.id}").measure > 0
    tube = math.pi * (sig.r_outer**2 - (sig.r_outer - sig.plating) ** 2)
    ref = len(st.vias) - 1
    full = (sig.z_top - 0.2e-3) * tube + ref * (sig.z_top - sig.z_bottom) * tube
    assert rep.copper_volume == pytest.approx(full, rel=1e-6)
    stats, _ = _check(model, tmp_path)
    assert stats.n_tets > 0


def test_glb_export(tmp_path: Path) -> None:
    p = via_transition_problem()
    model = build_occ_model(
        p.structure, sheet_threshold=1e-4, ports=p.ports, options=OccOptions(f_max=20e9)
    )
    out = tmp_path / "m.glb"
    counts = export_glb(model, out)
    assert "port:P1" in counts and any(k.startswith("via:") for k in counts)
    raw = out.read_bytes()
    magic, version, length = struct.unpack("<III", raw[:12])
    assert magic == 0x46546C67 and version == 2 and length == len(raw)


def test_msh_v2_written(tmp_path: Path) -> None:
    p = via_transition_problem()
    st = thick_board()
    model = build_occ_model(st, sheet_threshold=50e-6, options=OccOptions(f_max=1e9))
    stats = write_msh(model, tmp_path / "v2.msh", msh_version="2.2")
    assert (tmp_path / "v2.msh").read_text().startswith("$MeshFormat\n2.2")
    assert stats.n_tets > 0
    del p


def _unused() -> None:  # keep imported helpers referenced for static checkers
    _ = (Box3, Structure3D, ConductorMaterial, fr4_materials, struct)
