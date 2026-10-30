# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Multi-pole Debye fit of Djordjevic-Sarkar (ADR-0010 section 5, WP-510)."""

from __future__ import annotations

import numpy as np
import pytest

from oa_em_openems.dispersion import eval_eps, fit_debye, fit_debye_multi
from oa_model.materials import DjordjevicSarkar


@pytest.mark.parametrize("f_max", [10e9, 20e9])
def test_multipole_follows_the_flat_loss_tangent(f_max: float) -> None:
    ds = DjordjevicSarkar(dk=4.3, df=0.02, f_ref=1e9)
    single = fit_debye(ds, f_max / 20, f_max)
    multi = fit_debye_multi(ds, f_max / 20, f_max)
    assert single.max_rel_err_tand > 0.05  # what a single pole cannot do
    assert multi.max_rel_err_tand < 0.005 and multi.max_rel_err_eps < 0.002
    assert 2 <= len(multi.poles) <= 8
    f = np.linspace(f_max / 20, f_max, 7)
    got, want = multi.eval(f), eval_eps(ds, f)
    assert np.allclose(got, want, rtol=3e-3, atol=0)
    assert all(d > 0 and t > 0 for d, t in multi.poles) and multi.kappa >= 0
