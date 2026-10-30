# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Multi-port excitation batching: all ports as one batch, or a subset plus reciprocity."""

from __future__ import annotations

import numpy as np
import pytest
from fdtd_phys_helpers import plate_line

from oa_fdtd.model import ModelError
from oa_fdtd.run import run, sparams

FREQS = np.linspace(1e9, 8e9, 4)


def test_subset_excitation_matches_full_batch() -> None:
    full = plate_line(0.006, 0.25e-3, FREQS, 10e9, mode_ports=True)
    res_full = run(full, engine="numpy", dtype="float64", end_db=-60.0)
    s_full = sparams(full, res_full)
    assert res_full.probes["V:p1"].shape[0] == 2  # two excitations in one batch

    half = plate_line(0.006, 0.25e-3, FREQS, 10e9, mode_ports=True)
    half.excite = ("p1",)
    res_half = run(half, engine="numpy", dtype="float64", end_db=-60.0)
    assert res_half.probes["V:p1"].shape[0] == 1  # one excitation
    s_half = sparams(half, res_half)
    assert np.all(np.isnan(s_half[:, 1]))  # undriven column
    s_rec = sparams(half, res_half, reciprocal=True)
    assert np.isnan(s_rec[1, 1]).all()  # S22 needs port 2 driven
    known = ~np.isnan(s_rec)
    assert known[0, 1].all()  # S12 filled from S21
    assert np.abs(s_rec[known] - s_full[known]).max() < 1e-9
    assert np.abs(s_half[:, 0] - s_full[:, 0]).max() < 1e-12


def test_unknown_excited_port_is_an_error() -> None:
    m = plate_line(0.006, 0.25e-3, FREQS, 10e9, mode_ports=True)
    m.excite = ("nope",)
    with pytest.raises(ModelError) as exc:
        run(m, engine="numpy", end_db=-40.0)
    assert exc.value.code == "FDTD_BAD_DRIVE"
