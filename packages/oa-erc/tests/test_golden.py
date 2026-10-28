# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Analytic golden cases in tests/golden/erc (case.toml)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from oa_erc.rules.emi import cavity_modes
from oa_physics.via import stub_resonance

GOLDEN = Path(__file__).resolve().parents[3] / "tests" / "golden" / "erc"


def _case(name: str) -> dict:  # type: ignore[type-arg]
    return tomllib.loads((GOLDEN / name / "case.toml").read_text(encoding="utf-8"))


@pytest.mark.golden
def test_cavity_40x30() -> None:
    c = _case("cavity_40x30")
    i = c["inputs"]
    modes = {(m, n): f for f, m, n in cavity_modes(i["a"], i["b"], i["er"], 5e9)}
    assert modes[tuple(i["mode"])] == pytest.approx(
        c["expected"]["f_hz"], rel=c["tolerance"]["rtol"]
    )


@pytest.mark.golden
def test_via_stub_resonance() -> None:
    c = _case("via_stub_resonance")
    i = c["inputs"]
    assert stub_resonance(i["length"], i["dk"]) == pytest.approx(
        c["expected"]["f_hz"], rel=c["tolerance"]["rtol"]
    )
