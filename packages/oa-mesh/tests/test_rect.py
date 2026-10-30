# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""oa_mesh.rect: layer, copper-face, grading and d_max rules."""

from __future__ import annotations

import numpy as np
import pytest

from oa_mesh.rect import AxisSpec, CopperFace, build_axis, max_cell_size


def test_max_cell_size_medium_fr4() -> None:
    d = max_cell_size(10e9, 4.4, "medium")
    assert d == pytest.approx(299792458.0 / 10e9 / 4.4**0.5 / 20)


def test_presets_match_the_fdtd_family() -> None:
    """Coarse/medium/fine are 10/20/30 cells per wavelength, as in oa_fdtd and openEMS."""
    base = max_cell_size(10e9, 1.0, 10)
    for name, n in (("coarse", 10), ("medium", 20), ("fine", 30)):
        assert max_cell_size(10e9, 1.0, name) == pytest.approx(base * 10 / n)


def test_layers_copper_and_grading() -> None:
    h, t = 200e-6, 35e-6
    spec = AxisSpec(
        lo=-2e-3,
        hi=2e-3,
        d_max=300e-6,
        layers=(0.0, h),
        copper_faces=(CopperFace(h, +1.0), CopperFace(h + t, +1.0)),
    )
    m = build_axis(spec)
    lines = m.lines
    assert lines[0] == pytest.approx(-2e-3) and lines[-1] == pytest.approx(2e-3)
    inside = np.count_nonzero((lines > -1e-12) & (lines < h + 1e-12))
    assert inside >= 5  # at least 4 cells across the dielectric
    assert m.max_cell <= 300e-6 * (1 + 1e-9)
    assert m.max_grading <= 2.5 + 1e-9  # the thirds rule itself is a 2:1 step


def test_bad_axis() -> None:
    with pytest.raises(ValueError, match="hi > lo"):
        build_axis(AxisSpec(lo=1.0, hi=0.0, d_max=0.1))
