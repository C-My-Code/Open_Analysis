# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""S must not depend on how many output frequencies are requested (regression, WP-507)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fdtd_verify_cases as cases  # noqa: E402

from oa_fdtd.adapter.sim import solve_problem  # noqa: E402

pytestmark = pytest.mark.gpu

_FX = {"bem01": [5e9, 10e9, 15e9, 20e9], "bem03": [5e9, 10e9, 20e9, 30e9, 40e9]}


@pytest.mark.parametrize("case", ["bem01", "bem03"])
def test_s_independent_of_n_points(case: str) -> None:
    fx = _FX[case]
    ref = None
    for n in (20, 41, 80, 160):
        p = cases.microstrip_thru(n_points=n) if case == "bem01" else cases.diff_via(n_points=n)
        p = p.model_copy(
            update={"frequency": p.frequency.model_copy(update={"extra_points": tuple(fx)})}
        )
        r = solve_problem(p)
        idx = [int(np.argmin(np.abs(r.freqs - f))) for f in fx]
        s = r.s[idx]
        if ref is None:
            ref = s
        assert np.max(np.abs(s - ref)) <= 1e-6 * np.max(np.abs(ref)), n
