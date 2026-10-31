# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""IM-002 solves the BEM sections of many nets in one batch (WP-520); the violations equal the
net-by-net path (one ``solve_many`` per net, ``_BATCH_SECTIONS = 1``)."""

from __future__ import annotations

from typing import Any

import pytest
from erc_helpers import load_board
from erc_synthetic import width_sweep_board

from oa_erc import run_rules
from oa_erc.rules import impedance as im
from oa_framework.services import DictServices
from oa_impedance.service import ImpedanceServiceImpl
from oa_model.board import Board
from oa_model.setup.intent import DesignIntent, NetSelector, SignalIntent
from oa_results.violations import Violation

WIDTHS = tuple(round(0.1e-3 + 0.05e-3 * i, 6) for i in range(12))


class _Counting(ImpedanceServiceImpl):
    def __init__(self) -> None:
        super().__init__(max_workers=2)
        self.batches: list[int] = []

    def solve_many(self, xss: Any, freqs: Any = None, *, method: str = "auto") -> Any:
        if method != "closed_form":
            self.batches.append(len(xss))
        return super().solve_many(xss, freqs, method=method)


def _run(board: Board, intent: DesignIntent) -> tuple[list[str], _Counting]:
    svc = _Counting()
    vs: list[Violation] = run_rules(
        board, intent=intent, services=DictServices({"impedance": svc}), rule_ids=["IM-002"]
    )
    return [v.model_dump_json() for v in vs], svc


def _intent(target: float, tol: float) -> DesignIntent:
    return DesignIntent(
        signals=(
            SignalIntent(select=NetSelector(names=("*",)), target_z_se=target, z_tolerance=tol),
        )
    )


@pytest.mark.parametrize(
    ("board", "target", "tol"),
    [("sweep", 50.0, 0.10), ("sweep", 42.0, 0.15), ("combined", 40.0, 0.10)],
)
def test_batched_equals_net_by_net(
    board: str, target: float, tol: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = width_sweep_board(WIDTHS) if board == "sweep" else load_board(board)
    intent = _intent(target, tol)
    got, svc = _run(b, intent)
    with monkeypatch.context() as m:
        m.setattr(im, "_BATCH_SECTIONS", 1)
        want, svc_net = _run(b, intent)
    assert want, "the case must produce violations"
    assert got == want
    assert svc.stats["bem"] == svc_net.stats["bem"]
    # one batch for the screening pass (the board is small), plus the refinements of failing
    # runs (one section each); net by net, every net with an undecided section adds a call
    assert len(svc.batches) <= len(svc_net.batches)
    assert sum(svc.batches) == sum(svc_net.batches)
    assert max(svc.batches) >= max(svc_net.batches)
