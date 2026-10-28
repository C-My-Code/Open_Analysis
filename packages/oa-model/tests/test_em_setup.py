# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from boards import make_board, rect
from oa_model import (
    AnalysisSetup,
    DesignIntent,
    EMProblem,
    ProjectManifest,
    Structure3D,
    resolve_intent,
)
from oa_model.em import (
    Boundaries,
    Box3,
    ConductorBody,
    EMWavePort,
    FrequencyPlan,
    MaterialSlab,
    RegionProvenance,
)
from oa_model.materials import ConductorMaterial, ConstantDkDf, DielectricMaterial
from oa_model.qs import QSProblem, QSTerminal
from oa_model.setup import (
    LumpedPort,
    NearestReferenceTerminal,
    PadTerminal,
    TraceEndPort,
    load_intent,
    load_project,
    load_setup,
)
from oa_model.thermal import HeatSource, ThermalBC, ThermalProblem


def structure(dz: float = 0.0, prov: RegionProvenance | None = None) -> Structure3D:
    return Structure3D(
        bbox=Box3(xmin=0, ymin=0, zmin=0, xmax=0.01, ymax=0.01, zmax=0.001),
        materials=(
            DielectricMaterial(name="FR4", model=ConstantDkDf(dk=4.3, df=0.02)),
            ConductorMaterial(name="copper"),
        ),
        slabs=(MaterialSlab(material="FR4", z_bottom=0.0, z_top=0.0002 + dz),),
        conductors=(
            ConductorBody(
                id="c1",
                net="SIG",
                role="signal",
                layer="L1",
                z_bottom=0.0002 + dz,
                z_top=0.000235 + dz,
                polygons=(rect(0, 0.004, 0.01, 0.0041),),
            ),
        ),
        vias=(),
        provenance=prov or RegionProvenance(),
    )


def problem(name: str = "p", dz: float = 0.0) -> EMProblem:
    return EMProblem(
        name=name,
        structure=structure(dz=dz),
        ports=(
            EMWavePort(
                name="P1",
                face="xmin",
                rect=(0.0035, 0.0045, 0.0, 0.001),
                signal_conductors=("c1",),
                line_type="microstrip",
            ),
        ),
        boundaries=Boundaries(
            xmin="absorbing", xmax="absorbing", ymin="pec", ymax="pec", zmin="pec", zmax="pec"
        ),
        frequency=FrequencyPlan(f_min=0, f_max="10GHz"),  # type: ignore[arg-type]
    )


def test_structure_and_problem_keys() -> None:
    s = structure()
    assert s.key().startswith("st1:")
    assert s.key() == structure(prov=RegionProvenance(board_hash="x", nets=("SIG",))).key()
    assert s.key() != structure(dz=2e-9).key()
    assert s.key() == structure(dz=0.3e-9).key()
    p = problem("a")
    assert p.key() == problem("b").key()
    assert p.key().startswith("em1:")
    assert p.key() != problem("a", dz=2e-9).key()
    assert p.frequency.f_max == 10e9
    assert EMProblem.model_validate_json(p.model_dump_json()) == p


def test_reserved_types_construct() -> None:
    qs = QSProblem(
        structure=structure(),
        terminals=(QSTerminal(name="t1", net="SIG"),),
        want=frozenset({"C", "L"}),
    )
    assert QSProblem.model_validate_json(qs.model_dump_json()) == qs
    th = ThermalProblem(
        sources=(HeatSource(id="h1", kind="component", component="U1", power=1.5),),
        bc=ThermalBC(t_ambient="25degC"),  # type: ignore[arg-type]
    )
    assert th.bc.t_ambient == pytest.approx(298.15)
    assert ThermalProblem.model_validate_json(th.model_dump_json()) == th


INTENT = """
schema: oa-intent/1
signals:
  - select: {names: ["SIG_*"]}
    rise_time: 100ps
    io: true
  - select: {names: ["SIG_N"]}
    high_speed: false
power:
  - {net: "+3V3", v_nominal: 3.3, tolerance: 5%}
"""


def test_resolve_intent() -> None:
    board = make_board()
    intent = DesignIntent.model_validate(yaml.safe_load(INTENT))
    r = resolve_intent(board, intent)
    idx = r.index()
    assert [n for n, _ in r.by_net] == [n.id for n in board.nets]
    assert idx["GND"].kind == "ground"
    assert idx["+3V3"].kind == "power"
    assert idx["+3V3"].power is not None
    assert idx["+3V3"].power.tolerance == pytest.approx(0.05)
    p = idx["SIG_P"]
    assert p.high_speed is True
    assert p.io is True
    assert p.rise_time == pytest.approx(1e-10)
    assert p.f_max == pytest.approx(3.5e9)
    assert p.diff_pair == "dp:SIG"
    assert p.target_z_diff == 90.0  # parsed from the net class name 'USB_90ohm'
    assert p.target_z_se is None
    assert idx["SIG_N"].high_speed is False  # later entry overrides
    assert idx["SIG_N"].rise_time == pytest.approx(1e-10)


def test_resolve_intent_ground_heuristic_and_removal() -> None:
    board = make_board()
    intent = DesignIntent(ground_nets=("+3V3",), diff_pairs_remove=("dp:SIG",))
    idx = resolve_intent(board, intent).index()
    assert idx["+3V3"].kind == "ground"
    assert idx["SIG_P"].diff_pair is None
    assert idx["SIG_P"].target_z_se == 90.0  # no pair -> single-ended reading


def test_setup_and_manifest_yaml(tmp_path: Path) -> None:
    (tmp_path / "via-sparams.yaml").write_text(
        """
schema: oa-setup/1
analysis: em.sparams
config:
  solver: openems
  region: {nets: [PCIE_TX0_P, PCIE_TX0_N], margin: 2mm}
  frequency: {f_min: 0, f_max: 40GHz, n_points: 801}
ports:
  - {kind: trace_end, name: P1, nets: [PCIE_TX0_P, PCIE_TX0_N], side: xmin, modes: differential}
  - {kind: lumped, name: P2, z0: 75ohm,
     positive: {kind: pad, component: U1, pin: "3"}, negative: {kind: nearest_reference}}
""",
        encoding="utf-8",
    )
    s = load_setup(tmp_path / "via-sparams.yaml")
    assert isinstance(s, AnalysisSetup)
    assert s.name == "via-sparams"
    assert isinstance(s.ports[0], TraceEndPort)
    assert s.ports[0].modes == "differential"
    p2 = s.ports[1]
    assert isinstance(p2, LumpedPort)
    assert p2.z0 == 75.0
    assert isinstance(p2.positive, PadTerminal)
    assert isinstance(p2.negative, NearestReferenceTerminal)

    (tmp_path / "oa-project.yaml").write_text(
        "schema: oa-project/1\nname: demo\nboard: demo.kicad_pcb\nphysics: demo.oa-physics.yaml\n",
        encoding="utf-8",
    )
    m = load_project(tmp_path / "oa-project.yaml")
    assert isinstance(m, ProjectManifest)
    assert m.setups_dir == Path("oa/setups")
    assert m.read_options.refill_zones == "never"

    (tmp_path / "intent.yaml").write_text(INTENT, encoding="utf-8")
    assert load_intent(tmp_path / "intent.yaml").power[0].net == "+3V3"

    with pytest.raises(ValueError):
        AnalysisSetup.model_validate(
            {"schema": "oa-setup/2", "name": "x", "analysis": "a", "config": {}}
        )


def test_manifest_paths_are_portable() -> None:
    """Manifest paths serialize with '/' on every OS (str(Path) gives '\\' on Windows)."""
    default = ProjectManifest.model_json_schema()["properties"]["setups_dir"]["default"]
    assert default == "oa/setups"
    m = ProjectManifest.model_validate(
        {"schema": "oa-project/1", "name": "demo", "board": Path("boards") / "demo.kicad_pcb"}
    )
    assert isinstance(m.board, Path)
    doc = json.loads(m.model_dump_json())
    assert doc["board"] == "boards/demo.kicad_pcb"
    assert doc["setups_dir"] == "oa/setups"
    assert ProjectManifest.model_validate(doc) == m
