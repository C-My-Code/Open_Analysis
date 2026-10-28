# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""[verify] resolution of docs/research/02 sec. 1.6: embedded-microstrip cover exponent.

The primary source of the exponent (2.0 in the report text, 1.55 in the variant it mentions) was
not available; both candidates are scored against the BEM. Neither is better than ~1 % on
average, which is why the model is flagged approximate (+-3 %).
"""

from __future__ import annotations

import itertools

import _xs
import pytest

from oa_impedance import closed_form as cf
from oa_impedance.service import ImpedanceServiceImpl

GRID = list(itertools.product([(1.0, 4.3), (2.0, 4.3), (0.5, 3.0)], [0.25, 0.5, 1.0, 2.0]))


def _errors(exponent: float) -> list[float]:
    h = 1e-3
    out = []
    for (u, er), cov in GRID:
        xs = _xs.microstrip(u, er, t_over_h=0.01, cover=cov * h)
        r = ImpedanceServiceImpl().solve(xs, [0.0], method="bem")
        assert r.params.z0 is not None
        z = float(r.params.z0.array[0])
        c = cf.embedded_microstrip(u * h, h, 0.01 * h, er, cov * h, exponent=exponent)
        assert c.z0 is not None
        out.append(abs(c.z0 / z - 1))
    return out


@pytest.mark.parametrize("exponent", [1.55, 2.0])
def test_candidate_exponents_within_stated_accuracy(exponent: float) -> None:
    e = _errors(exponent)
    assert max(e) < 0.035
    assert sum(e) / len(e) < 0.015


def test_default_exponent_is_the_better_candidate() -> None:
    assert sum(_errors(1.55)) <= sum(_errors(2.0))
