# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""B-EM-03 in a closed lossless box conserves power (regression).

With PEC copper, df = 0 and PEC side walls the differential via must give
``|S11|^2 + |S21|^2 = 1``. The wave planes used to sit inside the via's stitching cage (the line
is only 2.5 mm long), where the three-plane formula does not hold, and reported 0.35-0.67 above
7 GHz. The planes are now confined to the uniform line; this 1.3 mm stretch cannot hold
well-conditioned planes, so S comes from the port sheets (with a ``FDTD.PORT_S`` note).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fdtd_verify_cases as cases

from oa_fdtd.adapter.sim import SolveOptions, solve_problem
from oa_model.materials import ConstantDkDf, DielectricMaterial

pytestmark = pytest.mark.gpu


def test_diff_via_closed_lossless_power_balance() -> None:
    p = cases.diff_via()
    st = p.structure
    mats = tuple(
        m.model_copy(update={"model": ConstantDkDf(dk=3.9, df=0.0)})
        if isinstance(m, DielectricMaterial)
        else m
        for m in st.materials
    )
    p = p.model_copy(update={"structure": st.model_copy(update={"materials": mats})})
    bnd = p.boundaries.model_copy(update={"ymax": "pec", "zmin": "pec", "zmax": "pec"})
    p = p.model_copy(update={"boundaries": bnd})
    r = solve_problem(p, SolveOptions(copper="pec", dtype="float32", end_db=-40.0))
    assert any(n.startswith("FDTD.PORT_S") for n in r.notes)
    power = np.abs(r.s[:, 0, 0]) ** 2 + np.abs(r.s[:, 1, 0]) ** 2
    band = r.freqs >= 1e9
    # the port-sheet S counts only the static modal part of the sheet power (a few % low)
    assert power[band].min() > 0.92
    assert power.max() < 1.01
