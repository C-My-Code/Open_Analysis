# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The committed WP-510 reference data agrees with its spec and with the in-repo solvers."""

from __future__ import annotations

import itertools
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
    assert all(b > a for a, b in itertools.pairwise(il))  # loss grows with f
    i10 = doc["freqs_hz"].index(10e9)
    assert 0.7 < il[i10] < 0.95  # the canonical line at 10 GHz
    assert 48.0 < ref["z0"][i10] < 51.0
    assert doc["error_estimate"]["il_line_db"][i10] < 0.01  # mesh error well inside 0.02 dB


def test_bem_agrees_with_r1_on_the_canonical_line() -> None:
    f = HERE / "R1" / "reference.json"
    if not f.exists():
        pytest.skip("R1 not generated")
    import gen_r1
    import numpy as np

    from oa_impedance.service import ImpedanceServiceImpl

    doc = json.loads(f.read_text(encoding="utf-8"))
    freqs = np.array(doc["freqs_hz"])
    res = ImpedanceServiceImpl().solve(rs.em_microstrip_xs(), freqs, method="bem")
    rl = res.rlgc
    assert rl is not None

    def one(a):  # type: ignore[no-untyped-def]
        return np.asarray(a.array)[:, 0, 0]

    casc = gen_r1.cascade(freqs, one(rl.R), one(rl.L), one(rl.G), one(rl.C), rs.EM_L)
    il_err = np.abs(casc["il_line_db"] - np.array(doc["reference"]["il_line_db"]))
    assert il_err.max() < 0.01  # dB over 25 mm, 0.1 .. 20 GHz (0.004 measured); gate is 0.05
    z_err = np.abs(casc["z0"] / np.array(doc["reference"]["z0"]) - 1)
    assert z_err.max() < 0.003


def test_bem_meets_the_b_imp_02_gates_on_all_24_references() -> None:
    """Z0/Zdiff within 0.5 % (1 % for pairs) and eps_eff within 0.3 % of the converged FEM."""
    import gen_set24 as g

    files = sorted((HERE / "B-IMP-02").glob("*/reference.json"))
    if len(files) != 24:
        pytest.skip("B-IMP-02 references not generated")
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        assert (
            doc["error_estimate_pct"]["z_pct"] < 0.2
            and doc["error_estimate_pct"]["eps_eff_pct"] < 0.2
        )
        sp = next(s for s in g.GEOMS if s.name == doc["geometry"]["name"])
        b = g.bem_values(g.build(sp, doc["geometry"]["copper_um"]), 1.0, sp.pair)
        ref = doc["reference"]
        z_tol = 0.01 if sp.pair else 0.005
        assert abs(b["z"] / ref["z"] - 1) < z_tol, doc["id"]
        assert abs(b["eps_eff"] / ref["eps_eff"] - 1) < 0.003, doc["id"]
