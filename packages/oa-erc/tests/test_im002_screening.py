# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""IM-002 screening (WP-516): the violations equal the all-BEM path, with fewer BEM solves.

The oracle is the pre-WP-516 behaviour: every kept section is solved by the BEM (``screen=False``
and the "section has no Z0 / Zdiff" skip switched off by patching ``_has_verdict``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from erc_helpers import BOARD_NAMES, INTENTS, load_board
from erc_synthetic import width_sweep_board

from oa_erc import run_rules
from oa_erc.remediation import TypedRemediation
from oa_erc.rules import impedance as im
from oa_framework.services import DictServices
from oa_impedance.service import ImpedanceServiceImpl
from oa_model.board import Board
from oa_model.setup.intent import DesignIntent, NetSelector, SignalIntent
from oa_results.violations import Violation


def _intent(target: float, tol: float = 0.10, names: tuple[str, ...] = ("*",)) -> DesignIntent:
    return DesignIntent(
        signals=(
            SignalIntent(select=NetSelector(names=names), target_z_se=target, z_tolerance=tol),
        )
    )


def _run(
    board: Board,
    intent: DesignIntent | None,
    cfg: dict[str, object] | None = None,
) -> tuple[list[Violation], ImpedanceServiceImpl]:
    svc = ImpedanceServiceImpl(max_workers=1)
    vs = run_rules(
        board,
        intent=intent,
        services=DictServices({"impedance": svc}),
        rule_ids=["IM-002"],
        rule_config={"IM-002": cfg or {}},
    )
    return vs, svc


def _all_bem(
    board: Board, intent: DesignIntent | None, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[Violation], ImpedanceServiceImpl]:
    with monkeypatch.context() as m:
        m.setattr(im.ImpedanceOutOfTolerance, "_has_verdict", staticmethod(lambda xs, need: True))
        return _run(board, intent, {"screen": False})


def _evidence(v: Violation) -> dict[str, Any]:
    assert v.remediation is not None
    return dict(TypedRemediation.from_model(v.remediation).evidence)


def _dump(vs: list[Violation]) -> list[str]:
    return [v.model_dump_json() for v in vs]


@pytest.mark.parametrize("name", BOARD_NAMES)
@pytest.mark.parametrize("target", [None, 40.0, 50.0, 60.0])
def test_wp102_boards_identical_to_all_bem(
    name: str, target: float | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    board = load_board(name)
    intent = INTENTS.get(name) if target is None else _intent(target)
    got, svc = _run(board, intent)
    want, svc_all = _all_bem(board, intent, monkeypatch)
    assert _dump(got) == _dump(want)
    assert svc.stats["bem"] <= svc_all.stats["bem"]


WIDTHS = tuple(round(0.10e-3 + 0.025e-3 * i, 9) for i in range(30))  # 0.10 .. 0.825 mm


@pytest.mark.parametrize(
    ("target", "tol"), [(50.0, 0.10), (60.0, 0.05), (42.0, 0.15), (50.0, 0.30), (55.0, 0.40)]
)
def test_width_sweep_identical_with_fewer_bem_solves(
    target: float, tol: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    board = width_sweep_board(WIDTHS)
    intent = _intent(target, tol, ("W*",))
    got, svc = _run(board, intent)
    want, svc_all = _all_bem(board, intent, monkeypatch)
    assert want, "the sweep must produce violations"
    assert _dump(got) == _dump(want)
    # clear passes are decided by the closed form: the BEM runs on the ambiguous sections and
    # on the candidates for the worst value of a failing run only
    assert svc.stats["closed_form"] > 0
    assert svc.stats["bem"] < svc_all.stats["bem"]
    if tol >= 0.3:  # a wide band: most sections pass clearly
        assert svc.stats["bem"] * 2 < svc_all.stats["bem"]


def test_screen_false_is_bem_everywhere() -> None:
    board = width_sweep_board(WIDTHS)
    vs, svc = _run(board, _intent(50.0, 0.10, ("W*",)), {"screen": False})
    assert svc.stats["closed_form"] == 0
    assert vs
    assert {_evidence(v)["accuracy_class"] for v in vs} == {"validated"}


def test_exact_values_false_reports_screening_with_bound() -> None:
    board = width_sweep_board(WIDTHS)
    intent = _intent(50.0, 0.10, ("W*",))
    exact, _ = _run(board, intent)
    fast, svc = _run(board, intent, {"exact_values": False})

    def ident(v: Violation) -> tuple[object, ...]:
        return (v.rule_id, v.nets, v.layers, v.items, v.location, v.limit)

    assert [ident(v) for v in fast] == [ident(v) for v in exact]
    n_screening = 0
    for f, e in zip(fast, exact, strict=True):
        ev = _evidence(f)
        if ev["accuracy_class"] == "screening":
            n_screening += 1
            b = ev["z_rel_bound"]
            assert 0 < b < 0.2
            lo, hi = ev["z_interval_ohm"]
            assert lo <= e.measured <= hi  # type: ignore[operator]  # the BEM value is inside
            assert abs(f.measured / e.measured - 1) <= b  # type: ignore[operator]
        else:
            assert ev["accuracy_class"] == "validated"
            assert f.measured == e.measured
    assert n_screening > 0
    assert svc.stats["bem"] < _run(board, intent, {"screen": False})[1].stats["bem"]


def test_other_methods_are_used_as_given() -> None:
    """``method="auto"`` is the caller's choice: no screening, no skip of sections."""
    board = width_sweep_board(WIDTHS)
    vs, svc = _run(board, _intent(50.0, 0.10, ("W*",)), {"method": "auto"})
    assert vs
    assert svc.stats["closed_form"] + svc.stats["bem"] >= 26
    assert {_evidence(v)["accuracy_class"] for v in vs} <= {"screening", "validated"}


def _mix_board(tracks: int) -> Board:
    from oa_ingest_kicad import KiCadReader
    from oa_model.setup import ReadOptions

    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "tools" / "bench"))
    try:
        import gen_synth_board  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    path = Path(__file__).parent / "_mix_tmp.kicad_pcb"
    path.write_text(gen_synth_board.generate(tracks, True), encoding="utf-8", newline="\n")
    try:
        return KiCadReader().read(path, ReadOptions())
    finally:
        path.unlink()


@pytest.mark.slow
def test_mix_board_identical_to_all_bem(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bus board: almost every section holds neighbouring signals (no Z0 to judge)."""
    board = _mix_board(800)
    intent = _intent(50.0, 0.10, ("N*",))
    got, svc = _run(board, intent)
    want, svc_all = _all_bem(board, intent, monkeypatch)
    assert _dump(got) == _dump(want)
    assert svc.stats["bem"] * 5 < svc_all.stats["bem"]


@pytest.mark.slow
def test_matrix_order_predicts_the_bem_quantity() -> None:
    """Z0 exists iff the RLGC order is 1 and Zdiff iff it is 2, so the skip is exact."""
    from oa_geometry import BoardGeometry

    board = _mix_board(300)
    geo = BoardGeometry.build(board)
    seen: dict[str, object] = {}
    for net in sorted({t.net for t in board.tracks if t.net}):
        total = sum(s.length for s in geo.segments(net))
        for s in geo.sample_net(net, ds=max(1e-3, total / 12)):
            seen.setdefault(s.xs.key(), s.xs)
    svc = ImpedanceServiceImpl(max_workers=1)
    xss = list(seen.values())
    orders = {im._matrix_order(x) for x in xss}
    assert len(orders) > 1
    for xs, r in zip(xss, svc.solve_many(xss, None, method="bem"), strict=True):
        order = im._matrix_order(xs)
        assert (r.params.z0 is not None) == (order == 1)
        assert (r.params.z_diff is not None) == (order == 2)
