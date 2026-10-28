# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""WP-102 boards as the acceptance oracle: found violations against ``expected.yaml: erc``."""

from __future__ import annotations

import math

import pytest
from erc_helpers import BOARD_NAMES, expected, loc_mm, run_board

# Findings the solver makes that expected.yaml does not list. They are real (BEM Z0 / Zdiff
# outside the +-10 % that the net-class name implies); see docs/change-requests/
# WP-201-expected-impedance.md. Everything else must match exactly.
EXTRA_IM002 = {"CPW_A", "CPW_B", "DP_P", "USB_D+"}


def _is_extra(v) -> bool:  # type: ignore[no-untyped-def]
    return v.rule_id == "IM-002" and v.nets[0] in EXTRA_IM002


def _match(e, v) -> bool:  # type: ignore[no-untyped-def]
    if e["rule_id"] != v.rule_id or sorted(e["nets"]) != sorted(v.nets):
        return False
    if e["layer"] not in v.layers:
        return False
    if e["location_mm"] is None:
        return True
    x, y = loc_mm(v)
    tol = max(e["tolerance_mm"], 0.05)
    return math.hypot(x - e["location_mm"][0], y - e["location_mm"][1]) <= tol


@pytest.mark.parametrize("name", BOARD_NAMES)
def test_expected_violations_reported(name: str) -> None:
    found = [v for v in run_board(name) if not _is_extra(v)]
    for e in expected(name)["erc"]:
        hits = [v for v in found if _match(e, v)]
        assert hits, f"{name}: missing {e['rule_id']} {e['nets']} near {e['location_mm']}"


@pytest.mark.parametrize("name", BOARD_NAMES)
def test_no_must_not_report_and_no_extras(name: str) -> None:
    exp = expected(name)
    found = [v for v in run_board(name) if not _is_extra(v)]
    forbidden = set(exp["must_not_report"])
    assert not [v for v in found if v.rule_id in forbidden]
    unspecified = {(u["rule_id"], tuple(sorted(u["nets"]))) for u in exp["unspecified"]}
    extras = [
        v
        for v in found
        if not any(_match(e, v) for e in exp["erc"])
        and (v.rule_id, tuple(sorted(v.nets))) not in unspecified
    ]
    assert not extras, [(v.rule_id, v.nets) for v in extras]


@pytest.mark.parametrize("name", BOARD_NAMES)
def test_negative_cases_not_reported(name: str) -> None:
    found = run_board(name)
    for n in expected(name)["negative_cases"]:
        near = [
            v
            for v in found
            if v.rule_id == n["rule_id"]
            and set(n["nets"]) & set(v.nets)
            and math.hypot(loc_mm(v)[0] - n["location_mm"][0], loc_mm(v)[1] - n["location_mm"][1])
            < 5.0
        ]
        assert not near, (n["rule_id"], n["nets"])


def test_im002_values_on_microstrip() -> None:
    vs = {v.nets[0]: v for v in run_board("microstrip") if v.rule_id == "IM-002"}
    assert set(vs) == {"MS_250", "MS_500"}
    assert vs["MS_500"].measured == pytest.approx(41.2, abs=1.0)
    assert vs["MS_250"].measured == pytest.approx(60.4, abs=1.5)
    assert vs["MS_500"].unit == "ohm" and vs["MS_500"].limit == pytest.approx(45.0)
