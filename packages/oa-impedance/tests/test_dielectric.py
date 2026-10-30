# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dielectric evaluation follows oa_physics: tabulated Dk/Df is interpolated in ln f."""

from __future__ import annotations

import math

import pytest

from oa_impedance._dielectric import eps_at
from oa_model.materials import TableDkDf
from oa_physics import eps_complex


def _table() -> TableDkDf:
    return TableDkDf(freqs=[1e8, 1e10], dk=[4.4, 4.0], df=[0.010, 0.020])


def test_table_interpolates_in_log_frequency() -> None:
    f = 1e9  # geometric midpoint of the table: ln-f weight 0.5 (linear-f weight 0.09)
    e = eps_at(_table(), f)
    assert e.real == pytest.approx(4.2, abs=1e-12)
    assert -e.imag / e.real == pytest.approx(0.015, abs=1e-12)
    assert e == complex(eps_complex(_table(), f)[0])


def test_static_request_uses_first_table_point() -> None:
    e = eps_at(_table(), 0.0)
    assert e.real == pytest.approx(4.4)
    assert math.isclose(-e.imag / e.real, 0.010)
