# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Region -> EMProblem generation: snapshot, ports, boundaries, errors."""

from __future__ import annotations

from typing import Any

import pytest
from em_boards import diff_via_board

from oa_em import EmSparamsConfig, build_problem
from oa_geometry import BoardGeometry
from oa_model.em import EMProblem, EMWavePort
from oa_model.errors import SetupError
from oa_testkit import builders as bld
from oa_testkit.golden import assert_close_to_golden

SUITE = "em_flow"
FREQ = {"f_min": 0, "f_max": 10e9, "n_points": 101}


def _cfg(nets: tuple[str, ...], **kw: Any) -> EmSparamsConfig:
    return EmSparamsConfig.model_validate(
        {"region": {"nets": nets, **kw.pop("region", {})}, "frequency": FREQ, **kw}
    )


def _flat(p: EMProblem) -> dict[str, Any]:
    st = p.structure
    out: dict[str, Any] = {
        "bbox": [
            st.bbox.xmin,
            st.bbox.ymin,
            st.bbox.zmin,
            st.bbox.xmax,
            st.bbox.ymax,
            st.bbox.zmax,
        ],
        "n_conductors": len(st.conductors),
        "conductors": [f"{c.id}:{c.role}" for c in st.conductors],
        "n_vias": len(st.vias),
        "boundaries": [
            getattr(p.boundaries, f) for f in ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")
        ],
        "air_padding": p.boundaries.air_padding or 0.0,
    }
    for pt in p.ports:
        assert isinstance(pt, EMWavePort)
        out[f"{pt.name}.face"] = pt.face
        out[f"{pt.name}.line_type"] = pt.line_type
        out[f"{pt.name}.signal"] = list(pt.signal_conductors)
        out[f"{pt.name}.rect"] = list(pt.rect)
        out[f"{pt.name}.excite"] = pt.excite
    return out


def test_microstrip_problem_matches_snapshot() -> None:
    geo = BoardGeometry.build(bld.microstrip_board())
    built = build_problem(geo, _cfg(("SIG",), region={"bbox": [1e-3, -3e-3, 24e-3, 3e-3]}))
    assert_close_to_golden(
        _flat(built.problem),
        SUITE,
        "microstrip_problem",
        source="regression snapshot of build_problem(microstrip_board, bbox 1..24 mm)",
        inputs={"board": "microstrip_board", "nets": "SIG"},
        rtol=1e-9,
        atol=1e-12,
    )


def test_microstrip_ports_are_wave_ports_on_the_faces() -> None:
    built = build_problem(
        BoardGeometry.build(bld.microstrip_board()),
        _cfg(("SIG",), region={"bbox": [1e-3, -3e-3, 24e-3, 3e-3]}),
    )
    ports = built.problem.ports
    assert [(p.name, p.face) for p in ports if isinstance(p, EMWavePort)] == [
        ("P1", "xmin"),
        ("P2", "xmax"),
    ]
    assert all(isinstance(p, EMWavePort) and p.line_type == "microstrip" for p in ports)
    b = built.problem.boundaries
    assert (b.zmin, b.zmax, b.ymin, b.ymax) == ("pec", "absorbing", "absorbing", "absorbing")


def test_auto_region_extends_trace_ends_to_the_faces() -> None:
    built = build_problem(BoardGeometry.build(bld.microstrip_board()), _cfg(("SIG",)))
    assert [d.code for d in built.diagnostics].count("EM.PORT_EXTENDED") == 2
    st = built.problem.structure
    body = next(c for c in st.conductors if c.id == "L1:SIG")
    xs = [x for poly in body.polygons for x in poly.exterior.array[:, 0]]
    assert min(xs) == pytest.approx(st.bbox.xmin) and max(xs) == pytest.approx(st.bbox.xmax)


def test_via_transition_single_ended_has_two_ports() -> None:
    geo = BoardGeometry.build(bld.via_transition_board())
    built = build_problem(geo, _cfg(("SIG",), region={"bbox": [1e-3, -3e-3, 24e-3, 3e-3]}))
    ports = [p for p in built.problem.ports if isinstance(p, EMWavePort)]
    assert [(p.name, p.face, p.signal_conductors) for p in ports] == [
        ("P1", "xmin", ("L1:SIG",)),
        ("P2", "xmax", ("L4:SIG",)),
    ]
    assert not built.want_mixed_mode and built.pairs == ()
    assert {v.id for v in built.problem.structure.vias if v.role == "signal"} == {"via:via:SIG:1"}


def test_via_transition_differential_has_four_ports_and_pairs() -> None:
    geo = BoardGeometry.build(diff_via_board())
    cfg = _cfg(
        ("SIG", "SIG_N"),
        region={"bbox": [1e-3, -3e-3, 24e-3, 3e-3]},
        ports=[
            {
                "kind": "trace_end",
                "name": "P1",
                "nets": ["SIG", "SIG_N"],
                "side": "xmin",
                "modes": "differential",
            },
            {
                "kind": "trace_end",
                "name": "P2",
                "nets": ["SIG", "SIG_N"],
                "side": "xmax",
                "modes": "differential",
            },
        ],
    )
    built = build_problem(geo, cfg)
    ports = [p for p in built.problem.ports if isinstance(p, EMWavePort)]
    assert [(p.name, p.face, p.signal_conductors) for p in ports] == [
        ("P1_P", "xmin", ("L1:SIG",)),
        ("P1_N", "xmin", ("L1:SIG_N",)),
        ("P2_P", "xmax", ("L4:SIG",)),
        ("P2_N", "xmax", ("L4:SIG_N",)),
    ]
    assert built.pairs == ((0, 1), (2, 3)) and built.want_mixed_mode


def test_diff_pair_board_auto_ports() -> None:
    geo = BoardGeometry.build(bld.diff_pair_board())
    built = build_problem(geo, _cfg(("DP_P", "DP_N")))
    names = [p.name for p in built.problem.ports]
    assert names == ["P1_P", "P1_N", "P2_P", "P2_N"]
    assert built.want_mixed_mode


def test_stripline_region_is_closed_with_pec_walls() -> None:
    geo = BoardGeometry.build(bld.stripline_board())
    built = build_problem(geo, _cfg(("SIG",), region={"bbox": [1e-3, -3e-3, 24e-3, 3e-3]}))
    b = built.problem.boundaries
    assert (b.ymin, b.ymax, b.zmin, b.zmax) == ("pec", "pec", "pec", "pec")
    assert (b.xmin, b.xmax) == ("absorbing", "absorbing")
    assert all(p.line_type == "stripline" for p in built.problem.ports if isinstance(p, EMWavePort))


def test_port_on_a_face_the_trace_does_not_cross_is_an_error() -> None:
    geo = BoardGeometry.build(bld.microstrip_board())
    cfg = _cfg(
        ("SIG",),
        region={"bbox": [1e-3, -3e-3, 24e-3, 3e-3]},
        ports=[{"kind": "trace_end", "name": "P1", "nets": ["SIG"], "side": "ymax"}],
    )
    with pytest.raises(SetupError) as ei:
        build_problem(geo, cfg)
    assert "EM.PORT_NOT_ON_FACE" in [d.code for d in ei.value.diagnostics]


def test_problem_is_deterministic() -> None:
    geo = BoardGeometry.build(bld.via_transition_board())
    cfg = _cfg(("SIG",))
    assert build_problem(geo, cfg).problem.key() == build_problem(geo, cfg).problem.key()
