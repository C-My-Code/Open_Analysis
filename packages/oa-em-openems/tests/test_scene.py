# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""EMProblem to scene translation: geometry, mesh and ports (no openEMS needed)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from oa_em_openems.scene import Options, build_scene, check_problem, frequency_grid
from oa_model.em import FarFieldRequest
from oa_testkit.emproblems import (
    microstrip_thru_problem,
    stripline_thru_problem,
    via_transition_problem,
)

C0 = 299792458.0


def _prims(scene, shape):  # type: ignore[no-untyped-def]
    return [p for p in scene.data["primitives"] if p["shape"] == shape]


def _sheet_model(p):  # type: ignore[no-untyped-def]
    """The single-sheet copper model (mesh and primitive rules of WP-108)."""
    expert = {"openems.conductor_model": "sheet"}
    return p.model_copy(update={"mesh": p.mesh.model_copy(update={"expert": expert})})


def test_microstrip_mesh_follows_the_rules() -> None:
    p = _sheet_model(microstrip_thru_problem())
    scene = build_scene(p)
    st = scene.stats
    # lambda/20 at f_max in the densest dielectric (eps' at 10 GHz is about 4.17)
    lam = C0 / (10e9 * math.sqrt(4.17))
    assert st["d_max_um"] <= lam / 20 * 1e6 * 1.01
    mesh = {a: np.array(scene.data["mesh"][a]) for a in "xyz"}
    for a in "xyz":
        assert np.diff(mesh[a]).max() <= st["d_max_um"] * (1 + 1e-6)
        assert (np.diff(mesh[a]) > 0).all()
    # >= 4 cells across the 200 um dielectric
    z = mesh["z"]
    inside = z[(z >= 35.0 - 1e-6) & (z <= 235.0 + 1e-6)]
    assert inside.size - 1 >= 4
    # third rule at the trace edges (y = +-190 um): lines 2/3 cell outside and 1/3 inside
    y = mesh["y"]
    d = st["d_edge_um"]
    assert np.isclose(y, 190.0 + 2 * d / 3, atol=1e-2).any()
    assert np.isclose(y, 190.0 - d / 3, atol=1e-2).any()
    assert np.isclose(y, -190.0 - 2 * d / 3, atol=1e-2).any()
    # grading in the cell sequence stays moderate everywhere
    assert max(st["max_grading"]) <= 1.5
    # the lumped port plane is a mesh line
    for port in scene.data["ports"]:
        assert port["start"][0] in set(np.round(mesh["x"], 3))


def test_microstrip_domain_and_boundaries() -> None:
    p = microstrip_thru_problem()
    scene = build_scene(p)
    x = scene.data["mesh"]["x"]
    assert x[0] < 0 and x[-1] > 25000.0  # padded through the absorbing x faces
    assert scene.data["boundary"] == ["PML_8", "PML_8", "PML_8", "PML_8", "PEC", "PML_8"]
    z = scene.data["mesh"]["z"]
    assert z[0] == 0.0  # PEC face at the bottom of the box: no padding below


def test_microstrip_geometry_primitives() -> None:
    scene = build_scene(_sheet_model(microstrip_thru_problem()))
    props = scene.data["properties"]
    sheets = {k: v for k, v in props.items() if v["kind"] == "sheet"}
    assert len(sheets) == 1  # 35 um copper, one conducting-sheet property
    sheet = next(iter(sheets.values()))
    assert sheet["conductivity"] == pytest.approx(5.8e7)
    assert sheet["thickness"] == pytest.approx(35e-6)
    polys = _prims(scene, "polygon")
    elevations = sorted(round(p["elevation"], 3) for p in polys)
    assert elevations == [35.0, 235.0]  # plane and trace sheets sit on the dielectric faces
    boxes = _prims(scene, "box")
    assert any(
        b["start"][2] == 35.0 and b["stop"][2] == 235.0 for b in boxes
    )  # the FR-4 slab, extended through the padding in xy
    slab = next(b for b in boxes if b["stop"][2] == 235.0)
    assert slab["start"][0] < 0 and slab["stop"][0] > 25000.0
    trace = next(p for p in polys if p["elevation"] == 235.0)
    xs = [pt[0] for pt in trace["points"]]
    assert min(xs) == 0.0 and max(xs) == 25000.0  # the trace ends at the lumped ports
    plane = next(p for p in polys if p["elevation"] == 35.0)
    assert min(pt[0] for pt in plane["points"]) < 0  # the plane continues under the padding


def test_microstrip_lumped_ports() -> None:
    scene = build_scene(microstrip_thru_problem())
    p1, p2 = scene.data["ports"]
    assert p1["type"] == "lumped" and p1["R"] == 50.0 and p1["exc_dir"] == "z"
    assert p1["start"] == [0.0, -190.0, 35.0] and p1["stop"] == [0.0, 190.0, 235.0]
    assert p2["start"][0] == 25000.0
    assert scene.data["excitation"]["f0"] > scene.data["excitation"]["fc"] * 1.7  # no DC content
    assert scene.data["end_criteria"] == pytest.approx(1e-5)  # -50 dB energy


def test_stripline_wave_ports_are_stripline_ports_with_feed_lines() -> None:
    scene = build_scene(stripline_thru_problem())
    p1, p2 = scene.data["ports"]
    assert p1["type"] == p2["type"] == "stripline"
    assert p1["prop_dir"] == "x" and p1["exc_dir"] == "z"
    # strip at mid-layer (235..270 um copper between dielectric on both sides)
    assert p1["start"][2] == p1["stop"][2] == pytest.approx(252.5)
    assert p1["height"] == pytest.approx(217.5)
    assert p1["start"][1:2] == [-100.0] and p1["stop"][1] == 100.0
    length = p1["length"]
    assert length >= 8 * scene.stats["d_max_um"] - 1e-6  # >= 8 cells of feed line
    # feed line sits outside the box, ending on the face; P2 mirrors it
    assert p1["start"][0] == pytest.approx(-length) and p1["stop"][0] == 0.0
    assert p2["start"][0] == pytest.approx(25000.0 + length) and p2["stop"][0] == 25000.0
    assert 0 < p1["feed_shift"] < p1["meas_shift"] < length
    assert p1["ref_shift"] == pytest.approx(length)  # reference plane on the box face
    # PEC planes close z and y; absorbers on x
    assert scene.data["boundary"] == ["PML_8", "PML_8", "PEC", "PEC", "PEC", "PEC"]
    # resin fills the copper layer between the two slabs
    boxes = _prims(scene, "box")
    assert any(b["start"][2] == 235.0 and b["stop"][2] == 270.0 for b in boxes)


def test_via_transition_vias_cylinders_and_antipads() -> None:
    p = via_transition_problem()
    scene = build_scene(p)
    cyl = _prims(scene, "cylinder")
    assert len(cyl) == 5  # signal via and four return vias
    assert all(c["radius"] == pytest.approx(150.0) for c in cyl)
    sig = next(c for c in cyl if c["start"][:2] == [12500.0, 0.0])
    assert sig["start"][2] == 0.0 and sig["stop"][2] == pytest.approx(940.0)
    # >= 4 cells across the via diameter
    x = np.array(scene.data["mesh"]["x"])
    inside = x[(x >= 12500 - 150 - 1e-6) & (x <= 12500 + 150 + 1e-6)]
    assert inside.size - 1 >= 4
    # planes with antipads become keyhole polygons: a bridge repeats a vertex pair
    planes = [pr for pr in _prims(scene, "polygon") if len(pr["points"]) > 8]
    assert planes
    # microstrip wave ports; outer strips on the dielectric face, inner planes mid-layer
    p1, p2 = scene.data["ports"]
    assert p1["type"] == p2["type"] == "msl"
    assert p1["start"][2] == pytest.approx(905.0) and p1["stop"][2] == pytest.approx(687.5)
    assert p2["start"][2] == pytest.approx(35.0) and p2["stop"][2] == pytest.approx(252.5)
    assert scene.data["boundary"] == ["PML_8"] * 6


def test_wave_port_without_reference_is_an_error() -> None:
    p = microstrip_thru_problem()
    assert not [d for d in check_problem(p) if d.severity == "error"]
    sl = stripline_thru_problem()
    bad = sl.model_copy(
        update={
            "structure": sl.structure.model_copy(
                update={"conductors": sl.structure.conductors[1:2]}
            )
        }
    )
    with pytest.raises(ValueError, match=r"OPENEMS\.PORT_NO_REFERENCE"):
        build_scene(bad)


def test_check_reports_unsupported_features() -> None:
    p = microstrip_thru_problem()
    far = p.model_copy(
        update={
            "outputs": p.outputs.model_copy(
                update={"farfield": FarFieldRequest(frequencies=(5e9,))}
            )
        }
    )
    codes = {d.code for d in check_problem(far)}
    assert "OPENEMS.FARFIELD_DISABLED" in codes
    assert "OPENEMS.FARFIELD_DISABLED" not in {
        d.code for d in check_problem(far, farfield_enabled=True)
    }


def test_frequency_grid_and_options() -> None:
    p = microstrip_thru_problem(n_points=201, f_max=10e9)
    f = frequency_grid(p)
    assert f.size == 201 and f[-1] == pytest.approx(10e9) and f[0] == pytest.approx(50e6)
    assert Options.from_problem(p).conductor_model == "skin"
    p2 = p.model_copy(
        update={"mesh": p.mesh.model_copy(update={"expert": {"openems.conductor_model": "pec"}})}
    )
    scene = build_scene(p2)
    assert any(v["kind"] == "metal" for v in scene.data["properties"].values())
    assert _prims(scene, "linpoly")


def test_skin_model_meshes_both_faces_with_a_pec_core_and_real_thickness_skins() -> None:
    p = microstrip_thru_problem(f_max=10e9)
    assert Options.from_problem(p).conductor_model == "skin"
    scene = build_scene(p)
    z = np.array(scene.data["mesh"]["z"])
    for face in (0.0, 35.0, 235.0, 270.0):  # plane faces, trace bottom and top [um]
        assert np.isclose(z, face, atol=1e-6).any(), face
    sheets = {k: v for k, v in scene.data["properties"].items() if v["kind"] == "sheet"}
    assert len(sheets) == 1
    (sheet,) = sheets.values()
    assert sheet["thickness"] == pytest.approx(35e-6) and sheet["conductivity"] == 5.8e7
    sheet_name = next(iter(sheets))
    mine = [q for q in scene.data["primitives"] if q["prop"] == sheet_name]
    # trace: top + bottom skin and the two axis-aligned side walls; the plane's bottom face is
    # on the PEC domain wall and has none
    assert sum(q["shape"] == "polygon" for q in mine) == 3
    walls = [q for q in mine if q["shape"] == "box"]
    assert any(
        abs(q["start"][2] - 235.0) < 1e-6 and abs(q["stop"][2] - 270.0) < 1e-6 for q in walls
    )
    # the PEC cores sit below the skins in priority, so the skins own the face edges
    cores = [q for q in scene.data["primitives"] if q["shape"] == "linpoly" and q["prop"] == "PEC"]
    assert len(cores) == 2
    assert all(q["priority"] < min(m["priority"] for m in mine) for q in cores)
    assert max(scene.stats["max_grading"]) <= 2.0  # the extra face lines stay gently graded


def test_skin_freq_option_sets_a_flat_surface_resistance() -> None:
    p = microstrip_thru_problem(f_max=10e9)
    expert = {"openems.conductor_model": "skin", "openems.skin_freq": 10e9}
    p = p.model_copy(update={"mesh": p.mesh.model_copy(update={"expert": expert})})
    (sheet,) = (v for v in build_scene(p).data["properties"].values() if v["kind"] == "sheet")
    rs = math.sqrt(math.pi * 10e9 * 4e-7 * math.pi / sheet["conductivity"])
    assert 1.0 / (sheet["conductivity"] * sheet["thickness"]) == pytest.approx(rs, rel=1e-6)


def test_translation_is_deterministic() -> None:
    a = build_scene(via_transition_problem())
    b = build_scene(via_transition_problem())
    assert a.data == b.data
    assert a.stats["mesh_hash"] == b.stats["mesh_hash"]


@pytest.mark.parametrize(
    ("preset", "expected"), [("coarse", 10.0), ("medium", 20.0), ("fine", 30.0)]
)
def test_presets_set_cells_per_wavelength(preset: str, expected: float) -> None:
    p = microstrip_thru_problem()
    p = p.model_copy(update={"mesh": p.mesh.model_copy(update={"preset": preset})})
    scene = build_scene(p)
    assert scene.stats["cells_per_wavelength"] == expected
