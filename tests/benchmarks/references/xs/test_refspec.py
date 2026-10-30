# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The committed WP-510 reference data agrees with its spec and with the in-repo solvers."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import refspec as rs  # noqa: E402


def test_canonical_spec_matches_refspec() -> None:
    spec = tomllib.loads((HERE / "specs" / "canonical-microstrip.toml").read_text(encoding="utf-8"))
    g, d = spec["geometry"], spec["dielectric"]
    assert g["line_width_m"] == rs.EM_W and g["dielectric_height_m"] == rs.EM_H
    assert g["copper_thickness_m"] == rs.EM_T and g["length_m"] == rs.EM_L
    assert spec["conductor"]["conductivity_S_per_m"] == rs.SIGMA_CU
    assert (d["dk_at_f_ref"], d["tan_delta_at_f_ref"], d["f_ref_hz"]) == (
        rs.EM_DK,
        rs.EM_DF,
        rs.EM_FREF,
    )


def test_richardson_recovers_a_geometric_sequence() -> None:
    seq = [1.0 + 0.5**k for k in range(4)]  # limit 1.0, ratio 0.5
    r = rs.richardson(seq)
    assert r["value"] == pytest.approx(1.0, abs=1e-12) and r["ratio"] == pytest.approx(0.5)
    flat = rs.richardson([1.0, 1.1, 1.05])  # not monotone: falls back to the finest value
    assert flat["value"] == 1.05


def test_r1_is_self_consistent() -> None:
    f = HERE / "R1" / "reference.json"
    if not f.exists():
        pytest.skip("R1 not generated")
    doc = json.loads(f.read_text(encoding="utf-8"))
    ref = doc["reference"]
    assert len(doc["freqs_hz"]) == len(ref["il_line_db"])
    il = ref["il_line_db"]
    assert all(b > a for a, b in zip(il, il[1:], strict=False))  # loss grows with f
    i10 = doc["freqs_hz"].index(10e9)
    assert 0.7 < il[i10] < 0.95  # the canonical line at 10 GHz
    assert 48.0 < ref["z0"][i10] < 51.0
    assert doc["error_estimate"]["il_line_db"][i10] < 0.01  # mesh error well inside 0.02 dB
