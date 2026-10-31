# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""B-EM-03 at the default mesh against the mesh-converged oa-fdtd (regression, WP-517).

The converged values are the first-order Richardson extrapolation (on the complex S; observed
ratio 2.4 per halving) of three via refinements
(``via_cells`` 4 / 8 / 16 with ``via_gap_cells`` 6 / 12 / 24; 1.1 / 2.7 / 8.4 M cells). The plain
``d_max`` mesh (4 cells across the antipad gap, return vias in 190 um cells) with the equal-area
square barrel was 0.62 dB low in SDD21 at 20 GHz; the default refinement is within 0.05 dB of
the extrapolation to 20 GHz and 0.06 dB to 40 GHz, half of the B-EM-03 tolerances. SDD11 is
within 1 dB to 37.5 GHz; the null near 40 GHz (-23 dB) is not converged (q8 -28.5 dB) and is
not checked here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fdtd_verify_cases as cases

from oa_fdtd.adapter.sim import solve_problem

pytestmark = pytest.mark.gpu

# f [Hz]: (SDD21 [dB], SDD11 [dB]) of the extrapolated (converged) mesh
_CONVERGED = {
    5e9: (-0.476, -10.81),
    10e9: (-1.292, -6.46),
    15e9: (-1.992, -4.84),
    20e9: (-2.279, -4.45),
    25e9: (-2.109, -4.91),
    30e9: (-1.578, -6.29),
    35e9: (-0.911, -9.82),
}


def test_diff_via_default_mesh_is_converged() -> None:
    p = cases.diff_via()
    p = p.model_copy(
        update={"frequency": p.frequency.model_copy(update={"extra_points": tuple(_CONVERGED)})}
    )
    r = solve_problem(p)
    assert any(n.startswith("FDTD.PORT_EXT") for n in r.notes)
    for f, (s21, s11) in _CONVERGED.items():
        k = int(np.argmin(np.abs(r.freqs - f)))
        assert abs(r.freqs[k] - f) < 1.0
        d21 = 20.0 * np.log10(abs(r.s[k, 1, 0])) - s21
        d11 = 20.0 * np.log10(abs(r.s[k, 0, 0])) - s11
        assert abs(d21) < (0.05 if f <= 20e9 else 0.1), (f, d21)
        assert abs(d11) < 0.75, (f, d11)
    # passive and reciprocal
    power = np.abs(r.s[:, 0, 0]) ** 2 + np.abs(r.s[:, 1, 0]) ** 2
    assert power.max() < 1.0 + 1e-3
    assert np.max(np.abs(r.s[:, 1, 0] - r.s[:, 0, 1])) < 1e-3
