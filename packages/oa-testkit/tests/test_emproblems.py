# SPDX-License-Identifier: GPL-3.0-or-later
"""EMProblem fixtures: structure, ports and JSON round trip."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from oa_model.em import EMLumpedPort, EMProblem, EMWavePort

from oa_testkit import emproblems as emp

FACTORIES: dict[str, Callable[[], EMProblem]] = {
    "microstrip": emp.microstrip_thru_problem,
    "stripline": emp.stripline_thru_problem,
    "via": emp.via_transition_problem,
}


@pytest.fixture(params=sorted(FACTORIES))
def problem(request: pytest.FixtureRequest) -> EMProblem:
    return FACTORIES[request.param]()


def test_json_round_trip_and_key(problem: EMProblem) -> None:
    again = EMProblem.model_validate_json(problem.model_dump_json())
    assert again == problem
    assert again.key() == problem.key()


def test_two_ports(problem: EMProblem) -> None:
    assert [p.name for p in problem.ports] == ["P1", "P2"]


@pytest.mark.parametrize("name", sorted(FACTORIES))
def test_deterministic(name: str) -> None:
    assert FACTORIES[name]() == FACTORIES[name]()
    assert FACTORIES[name]().key() == FACTORIES[name]().key()


def test_conductors_inside_bbox(problem: EMProblem) -> None:
    box = problem.structure.bbox
    for c in problem.structure.conductors:
        assert box.zmin <= c.z_bottom < c.z_top <= box.zmax
        for poly in c.polygons:
            a = poly.exterior.array
            assert a[:, 0].min() >= box.xmin - 1e-12 and a[:, 0].max() <= box.xmax + 1e-12
            assert a[:, 1].min() >= box.ymin - 1e-12 and a[:, 1].max() <= box.ymax + 1e-12


def test_microstrip_lumped_ports() -> None:
    p = emp.microstrip_thru_problem()
    assert all(isinstance(q, EMLumpedPort) and q.z0 == 50.0 for q in p.ports)
    assert p.ports[0].positive.position[0] == 0.0
    assert p.ports[1].positive.position[0] == pytest.approx(25e-3)
    assert p.ports[0].positive.z > p.ports[0].negative.z
    assert p.structure.bbox.xmax - p.structure.bbox.xmin == pytest.approx(25e-3)


def test_stripline_wave_ports() -> None:
    p = emp.stripline_thru_problem()
    assert all(isinstance(q, EMWavePort) and q.line_type == "stripline" for q in p.ports)
    assert [q.face for q in p.ports] == ["xmin", "xmax"]  # type: ignore[union-attr]
    ids = {c.id for c in p.structure.conductors}
    assert all(set(q.signal_conductors) <= ids for q in p.ports)  # type: ignore[union-attr]


def test_via_transition() -> None:
    p = emp.via_transition_problem()
    assert all(isinstance(q, EMWavePort) and q.line_type == "microstrip" for q in p.ports)
    sig = [v for v in p.structure.vias if v.role == "signal"]
    assert len(sig) == 1
    assert sig[0].z_bottom < min(c.z_bottom for c in p.structure.conductors if c.layer == "L2")
    assert sig[0].z_top > max(c.z_top for c in p.structure.conductors if c.layer == "L3")
    planes = [c for c in p.structure.conductors if c.role == "reference"]
    assert len(planes) == 2 and all(len(c.polygons[0].holes) == 1 for c in planes)
