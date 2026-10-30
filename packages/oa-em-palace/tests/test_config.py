# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``config.json`` generation: snapshots for the three EMProblem fixtures and the mapping rules."""

from __future__ import annotations

import json
from typing import Any

import pytest
from palace_helpers import FIXTURES, GOLDEN, coarse, groups

from oa_em_palace import PalaceOptions, build_config
from oa_model.em import Eigenmode
from oa_testkit.golden import update_mode


def _config(name: str, **kw: Any) -> tuple[dict[str, Any], Any]:
    return build_config(coarse(FIXTURES[name]()), groups(name), mesh_file="model.msh", **kw)


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_config_matches_snapshot(name: str) -> None:
    cfg, _ = _config(name)
    path = GOLDEN / f"config_{name}.json"
    text = json.dumps(cfg, indent=2) + "\n"
    if update_mode():
        path.write_text(text, encoding="utf-8", newline="\n")
    assert json.loads(text) == json.loads(path.read_text(encoding="utf-8"))
    assert text == path.read_text(encoding="utf-8").replace("\r\n", "\n")


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_every_attribute_exists_in_the_mesh_groups(name: str) -> None:
    cfg, _ = _config(name)
    g = groups(name)
    surf = {x["tag"] for x in g["groups"] if x["dim"] == 2}
    vol = {x["tag"] for x in g["groups"] if x["dim"] == 3}
    used_vol = {a for m in cfg["Domains"]["Materials"] for a in m["Attributes"]}
    assert used_vol == vol
    used: list[int] = []
    for key, val in cfg["Boundaries"].items():
        for entry in val if isinstance(val, list) else [val]:
            used.extend(entry["Attributes"])
        assert key in {
            "PEC",
            "PMC",
            "Absorbing",
            "Conductivity",
            "LumpedPort",
            "WavePort",
            "WavePortPEC",
        }
    assert set(used) <= surf
    # every exterior face, conductor and port is assigned (an unassigned face is natural PMC)
    mapped = {a for e in cfg["Boundaries"].get("Conductivity", []) for a in e["Attributes"]}
    mapped |= set(cfg["Boundaries"].get("PEC", {}).get("Attributes", []))
    mapped |= set(cfg["Boundaries"].get("Absorbing", {}).get("Attributes", []))
    for key in ("LumpedPort", "WavePort"):
        for e in cfg["Boundaries"].get(key, []):
            mapped |= set(e["Attributes"])
    assert mapped == surf


def test_microstrip_uses_lumped_ports_sheet_copper_and_absorbing_faces() -> None:
    cfg, info = _config("microstrip_thru")
    b = cfg["Boundaries"]
    assert [p["Index"] for p in b["LumpedPort"]] == [1, 2]
    assert all(p["R"] == 50.0 for p in b["LumpedPort"])
    # one excitation group per port: "true" would drive both ports at once
    assert [p["Excitation"] for p in b["LumpedPort"]] == [1, 2]
    assert all(p["Direction"] == "-Z" for p in b["LumpedPort"])  # trace (+) above the plane (-)
    assert "WavePort" not in b and "PEC" not in b  # the L2 plane is the sheet at the mesh bottom
    cond = {e["Attributes"][0]: e for e in b["Conductivity"]}
    assert set(cond) == {100, 101}
    assert all(e["Conductivity"] == 5.8e7 for e in cond.values())
    assert cond[100]["Thickness"] == pytest.approx(35e-6)  # 1 oz sheet keeps its thickness
    assert cond[101].get("External") is True and "External" not in cond[100]
    assert b["Absorbing"]["Order"] == 1
    assert info.port_index == {"P1": 1, "P2": 2}


def test_units_sweep_and_materials() -> None:
    cfg, info = _config("microstrip_thru")
    assert cfg["Model"]["L0"] == 1.0 and cfg["Model"]["Mesh"] == "model.msh"
    assert cfg["Problem"] == {"Type": "Driven", "Verbose": 2, "Output": "postpro"}
    (block,) = cfg["Solver"]["Driven"]["Samples"]
    # f_min = 0 is not solvable: the sweep starts at 10 % of f_max, in GHz
    assert block == {"Type": "Linear", "MinFreq": 1.0, "MaxFreq": 10.0, "NSample": 201}
    assert cfg["Solver"]["Order"] == 2 and cfg["Solver"]["Device"] == "CPU"
    assert "AdaptiveTol" not in cfg["Solver"]["Driven"]
    fr4 = next(m for m in cfg["Domains"]["Materials"] if m["Attributes"] == [1])
    assert fr4["Permittivity"] == pytest.approx(4.3, rel=0.03)
    assert fr4["LossTan"] == pytest.approx(0.02, rel=0.1)
    air = next(m for m in cfg["Domains"]["Materials"] if m["Attributes"] == [2])
    assert air["Permittivity"] == 1.0 and air["LossTan"] == 0.0
    codes = {d.code for d in info.diagnostics}
    assert "PALACE.NO_DC" in codes
    assert info.eps_freq == 5e9
    assert info.dielectric_fit["FR4_generic"]["f_fit_hz"] == 5e9


def test_stripline_wave_ports_and_closed_walls() -> None:
    cfg, _ = _config("stripline_thru")
    b = cfg["Boundaries"]
    assert [p["Index"] for p in b["WavePort"]] == [1, 2]
    assert all(p["Mode"] == 1 and p["Offset"] == 0.0 for p in b["WavePort"])
    # y walls are PEC; the L1/L3 planes at z min/max are external sheets
    assert b["PEC"]["Attributes"] == [2002, 2003]
    assert {e["Attributes"][0] for e in b["Conductivity"] if e.get("External")} == {
        g["tag"]
        for g in groups("stripline_thru")["groups"]
        if g["name"] in {"cond:cb:L1:GND", "cond:cb:L3:GND"}
    }
    # the 2D mode solve sees the conductors as PEC
    assert set(b["WavePortPEC"]["Attributes"]) == {e["Attributes"][0] for e in b["Conductivity"]}


def test_via_transition_covers_via_barrels_and_deembedding() -> None:
    cfg, _ = _config("via_transition")
    g = groups("via_transition")
    via_tags = {x["tag"] for x in g["groups"] if x["kind"] == "via"}
    cond = {e["Attributes"][0]: e for e in cfg["Boundaries"]["Conductivity"]}
    assert via_tags <= set(cond)
    assert all(cond[t]["Thickness"] > 0 for t in via_tags)  # plated tube thickness
    assert all(not cond[t].get("External") for t in via_tags)


def test_pec_conductor_model_and_second_order_abc() -> None:
    p = coarse(FIXTURES["microstrip_thru"]())
    p = p.model_copy(
        update={
            "mesh": p.mesh.model_copy(
                update={"expert": {"palace.conductor_model": "pec", "palace.abc_order": 2}}
            )
        }
    )
    cfg, _ = build_config(p, groups("microstrip_thru"), mesh_file="m.msh")
    b = cfg["Boundaries"]
    assert "Conductivity" not in b and {100, 101} <= set(b["PEC"]["Attributes"])
    assert b["Absorbing"]["Order"] == 2


def test_adaptive_sweep_amr_and_device_options() -> None:
    p = coarse(FIXTURES["microstrip_thru"]())
    p = p.model_copy(
        update={
            "frequency": p.frequency.model_copy(update={"adaptive": True, "spacing": "log"}),
            "mesh": p.mesh.model_copy(update={"amr_max_passes": 3, "fem_order": 3}),
        }
    )
    cfg, _ = build_config(
        p, groups("microstrip_thru"), mesh_file="m.msh", options=PalaceOptions(device="GPU")
    )
    assert cfg["Solver"]["Driven"]["AdaptiveTol"] == 1e-3
    assert cfg["Solver"]["Driven"]["Samples"][0]["Type"] == "Log"
    assert cfg["Solver"]["Order"] == 3 and cfg["Solver"]["Device"] == "GPU"
    ref = cfg["Model"]["Refinement"]
    assert ref["MaxIts"] == 3 and ref["Tol"] == 0.02


def test_unsupported_physics_is_a_value_error() -> None:
    p = coarse(FIXTURES["microstrip_thru"]()).model_copy(
        update={"physics": Eigenmode(f_target=1e9)}
    )
    with pytest.raises(ValueError, match="fullwave_driven"):
        build_config(p, groups("microstrip_thru"), mesh_file="m.msh")


def test_plan_bands_bound_the_dielectric_change_and_cover_the_sweep() -> None:
    from oa_em_palace import plan_bands
    from oa_em_palace.config import _band_deviation, sweep_frequencies

    problem = coarse(FIXTURES["microstrip_thru"]())
    opts = PalaceOptions(banded=True)
    bands = plan_bands(problem, opts)
    freqs = sweep_frequencies(problem, opts)
    assert len(bands) > 1
    assert sum(len(b.freqs) for b in bands) == freqs.size
    for b in bands:
        assert b.f_lo <= b.f_fit <= b.f_hi
        d_er, d_td = _band_deviation(problem, b.f_fit, b.f_lo, b.f_hi)
        assert d_er <= opts.eps_tol and d_td <= opts.tand_tol
    cfg, info = build_config(problem, groups("microstrip_thru"), mesh_file="model.msh", band=bands[0])
    assert cfg["Solver"]["Driven"]["Samples"][0]["Type"] == "Point"
    assert info.eps_freq == bands[0].f_fit
