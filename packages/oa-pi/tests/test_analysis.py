# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``pi.dc`` analysis end to end through the inline harness, refusals and options."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from pi_helpers import PLANE_CFG, read_kicad

from oa_framework.registry import Registry
from oa_geometry import BoardGeometry
from oa_model.errors import SetupError
from oa_model.setup import AnalysisSetup
from oa_pi import PdnService, PiDcAnalysis
from oa_pi.dc.config import DcConfig
from oa_pi.dc.fem import solve_fem
from oa_pi.dc.network import build_network
from oa_results.bundle import ResultBundle
from oa_testkit.builders import power_plane_board
from oa_testkit.harness import run_inline_full


def _setup(config: dict[str, Any]) -> AnalysisSetup:
    return AnalysisSetup(schema="oa-setup/1", name="dc", analysis="pi.dc", config=config)


def _run(board: Any, config: dict[str, Any]) -> Any:
    return run_inline_full(PiDcAnalysis, board, _setup(config), geometry=BoardGeometry.build)


@pytest.fixture(scope="module")
def plane_run() -> Any:
    return _run(read_kicad("power_plane"), PLANE_CFG)


def test_bundle_content_and_accuracy(plane_run: Any) -> None:
    bd: ResultBundle = plane_run.bundle
    assert bd.status == "ok"
    assert [i.id for i in bd.items] == ["fields", "loads", "vias", "pins", "rail"]
    assert [i.kind for i in bd.items] == ["field", "table", "table", "table", "scalars"]
    for it in bd.items:
        assert it.data.accuracy_class == "validated"
        (flag,) = it.data.validity_flags
        assert flag.code == "accuracy.2d_sheet"
        assert it.data.provenance is not None
        assert it.data.provenance.solver == "oa-dc-fem-p1"
        s = it.data.summary()
        assert len(json.dumps(s)) <= 2048  # per-item cap (ai-integration 4.2)
        assert len(it.data.digest()) <= 240
    assert len(json.dumps(bd.summary())) <= 8192
    assert "Worst load drop" in bd.summary_text
    assert "Worst via" in bd.summary_text
    assert bd.summary_text in bd.digest()
    assert len(bd.summary_text) <= 200
    field = bd.items[0].data
    assert {p.mesh.layer for p in field.pieces} == {"L2", "L3", "L4"}
    assert [f.name for f in field.pieces[0].fields] == ["V", "dV", "J_mag"]


def test_headline_numbers(plane_run: Any) -> None:
    sc = {s.name: s for s in plane_run.bundle.items[-1].data.values}
    assert sc["worst_load_drop"].status == "pass"
    assert sc["worst_load_drop"].limit == pytest.approx(0.03)
    assert 0.005 < sc["worst_load_drop"].value < 0.03
    assert sc["kcl_residual"].value < 1e-9
    assert sc["power_balance_error"].value == pytest.approx(0.0, abs=1e-6)
    assert sc["worst_via_current"].value > 0
    loads = plane_run.bundle.items[1].data
    assert loads.inline[0][0] == "U1"
    assert loads.inline[0][9] == "pass"
    vias = plane_run.bundle.items[2].data.inline
    assert abs(vias[0][4]) >= abs(vias[-1][4])  # worst first


def test_failing_load_sets_warning() -> None:
    cfg = PLANE_CFG | {"limits": {"drop_fraction": 0.001}}
    run = _run(read_kicad("power_plane"), cfg)
    assert run.bundle.status == "warning"
    assert any(d.code == "PI.LOAD_DROP_EXCEEDED" and d.hint for d in run.bundle.diagnostics)
    assert "FAIL" in run.bundle.summary_text


def test_via_density_limit() -> None:
    cfg = PLANE_CFG | {"limits": {"via_current_density": 1e6}}
    run = _run(read_kicad("power_plane"), cfg)
    assert any(d.code == "PI.VIA_CURRENT_DENSITY" for d in run.bundle.diagnostics)


def test_unfilled_zone_is_refused() -> None:
    board = read_kicad("power_plane")
    zones = tuple(
        z.model_copy(update={"fill_state": "unfilled"}) if z.net == "+1V0" else z
        for z in board.zones
    )
    with pytest.raises(SetupError) as ei:
        _run(board.model_copy(update={"zones": zones}), PLANE_CFG)
    d = ei.value.diagnostics[0]
    assert d.code == "PI_ZONE_UNFILLED"
    assert d.path and d.hint and "Refill" in d.hint


def test_stale_zone_warns() -> None:
    board = read_kicad("power_plane")
    zones = tuple(
        z.model_copy(update={"fill_state": "stale_suspected"}) if z.net == "+1V0" else z
        for z in board.zones
    )
    run = _run(board.model_copy(update={"zones": zones}), PLANE_CFG)
    assert any(d.code == "PI.ZONE_STALE" for d in run.diagnostics + run.bundle.diagnostics)


@pytest.mark.parametrize(
    ("patch", "path"),
    [
        ({"rail": {"net": "NOPE", "return": "GND"}}, "/config/rail/net"),
        ({"vrm": {"component": "U99"}}, "/config/vrm/pins"),
        ({"vrm": {"component": "U5", "pins": ["99"]}}, "/config/vrm/pins"),
        ({"loads": [{"component": "U1", "current": 1.0, "nets": ["GND"]}]}, "/config/loads/0/nets"),
    ],
)
def test_structured_setup_errors(patch: dict[str, Any], path: str) -> None:
    with pytest.raises(SetupError) as ei:
        _run(read_kicad("power_plane"), PLANE_CFG | patch)
    d = ei.value.diagnostics[0]
    assert d.path == path
    assert d.hint


def test_thevenin_output_resistance() -> None:
    board = power_plane_board()
    base = {
        "rail": {"net": "VDD", "return": "GND"},
        "loads": [{"component": "U2", "current": 5.0}],
        "temperature": 293.15,
    }

    def solve(vrm: dict[str, Any]) -> Any:
        cfg = DcConfig.model_validate(base | {"vrm": vrm})
        return solve_fem(build_network(board, BoardGeometry.build(board), cfg))

    ideal = solve({"component": "U1", "vset": 1.0})
    thev = solve({"component": "U1", "vset": 1.0, "r_out": 2e-3})
    assert thev.v_source == pytest.approx(1.0 - 2e-3 * 5.0, rel=1e-9)
    assert thev.i_source == pytest.approx(5.0, rel=1e-9)
    assert ideal.v_source == 1.0
    assert thev.loss_source == pytest.approx(2e-3 * 25.0)


def test_remote_sense_regulates_at_the_load() -> None:
    board = read_kicad("power_plane")
    pads = [p for p in board.pads if p.component == "U1"]
    pos = next(p for p in pads if p.net == "+1V0")
    neg = next(p for p in pads if p.net == "GND")
    cfg = DcConfig.model_validate(
        PLANE_CFG
        | {
            "vrm": {
                "component": "U5",
                "vset": 1.0,
                "sense": {"pos": f"U1.{pos.number}", "neg": f"U1.{neg.number}"},
            }
        }
    )
    sol = solve_fem(build_network(board, BoardGeometry.build(board), cfg))
    v_load = sol.group_voltage["load:U1"] - sol.group_voltage["load_ret:U1"]
    assert v_load == pytest.approx(1.0, abs=1e-9)
    assert sol.v_source > 1.0  # the regulator raises its output to cover the drop
    assert sol.residual < 1e-9


def test_equal_split_load_model() -> None:
    cfg = PLANE_CFG | {"loads": [{"component": "U1", "current": "10A", "model": "equal_split"}]}
    run = _run(read_kicad("power_plane"), cfg)
    rows = [r for r in run.bundle.items[3].data.inline if r[1] == "load"]
    assert len(rows) == 16
    assert all(r[7] == pytest.approx(10.0 / 16, rel=1e-9) for r in rows)


def test_no_return_net_is_ideal_return() -> None:
    board = power_plane_board()
    cfg = {
        "rail": {"net": "VDD"},
        "vrm": {"component": "U1"},
        "loads": [{"component": "U2", "current": 5.0}],
    }
    run = _run(board, cfg)
    row = run.bundle.items[1].data.inline[0]
    assert row[3] == 0.0  # ideal return: 0 V at the load return
    assert row[5] > 0


def test_registry_and_pdn_service_temperature() -> None:
    reg = Registry.for_tests(analyses=[PiDcAnalysis])
    assert reg.analysis("pi.dc") is PiDcAnalysis
    board = power_plane_board()
    cfg = {
        "rail": {"net": "VDD", "return": "GND"},
        "vrm": {"component": "U1"},
        "loads": [{"component": "U2", "current": 5.0}],
    }
    svc = PdnService().bind(board)
    cold = svc.dc_solve(cfg, 293.15)
    hot = svc.dc_solve(cfg, lambda layer, xy: 373.15 + 0 * xy[:, 0])
    d = [1.0 - s.group_voltage["load:U2"] + s.group_voltage["load_ret:U2"] for s in (cold, hot)]
    assert 1.25 < d[1] / d[0] < 1.32  # rho(T): 1 + 3.93e-3 * 80 = 1.314
    with pytest.raises(RuntimeError):
        PdnService().dc_solve(cfg)


@pytest.mark.slow
def test_three_hundred_thousand_unknowns_under_20_s() -> None:
    from pi_helpers import make_network, make_pin, make_sheet
    from shapely import Polygon as ShPolygon

    side = 0.1
    plate = ShPolygon([(0, 0), (side, 0), (side, side), (0, side)])
    p1 = ShPolygon([(0, 0), (1e-3, 0), (1e-3, side), (0, side)])
    p2 = ShPolygon([(side - 1e-3, 0), (side, 0), (side, side), (side - 1e-3, side)])
    net = make_network(
        [make_sheet("L1", "N", [plate])],
        make_pin("p1", "J1", "L1", "N", p1),
        make_pin("p2", "J2", "L1", "N", p2),
        h_plane=0.2e-3,
    )
    t0 = time.perf_counter()
    sol = solve_fem(net)
    dt = time.perf_counter() - t0
    assert sol.n_unknowns > 2.5e5
    assert dt < 20.0, (dt, sol.timings)
    exact = 0.098 / side * 4.926108374384237e-4 * (1 / (1.0)) * (1.0)
    assert 1.0 - sol.group_voltage["load:J2"] == pytest.approx(exact, rel=0.002)
