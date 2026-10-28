# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Quality gate, mixed-mode and reference-plane tests (WP-106)."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
import skrf
from synth import line_network

from oa_network import (
    QualityCriteria,
    assess,
    enforce_passivity,
    gate,
    make_dc,
    renormalize,
    se2gmm,
    shift_reference_plane,
)
from oa_testkit.golden import assert_close_to_golden, golden_root

CASES = (
    "passive_line",
    "passivity_violation",
    "non_reciprocal",
    "non_causal_const_dk",
    "starts_at_10mhz",
)


def _load(case: str) -> skrf.Network:
    return skrf.Network(str(golden_root() / "network_quality" / case / "line.s2p"))


@pytest.mark.parametrize("case", CASES)
def test_fixture_verdicts(case: str) -> None:
    rep = assess(_load(case))
    assert_close_to_golden({"verdict": rep.verdict, "dc": rep.dc}, "network_quality", case)


def test_passive_line_metrics() -> None:
    rep = assess(_load("passive_line"))
    assert rep.pqm == pytest.approx(100.0)
    assert rep.rqm == pytest.approx(100.0)
    assert rep.cqm > 95.0
    assert rep.max_singular_value <= 1.0 + 1e-6
    assert rep.worst_passivity_freq is None
    assert rep.n_freqs == 201 and rep.f_max == pytest.approx(20e9)


def test_passivity_violation_detected() -> None:
    rep = assess(_load("passivity_violation"))
    assert rep.max_singular_value > 1.001
    assert rep.worst_passivity_freq is None or rep.worst_passivity_freq > 0.0


def test_non_reciprocal_fails_rqm() -> None:
    rep = assess(_load("non_reciprocal"))
    assert rep.rqm < 99.0 and rep.reciprocity_max_err > 0.1
    assert rep.pqm == pytest.approx(100.0)


def test_non_causal_fixture_warns_on_cqm() -> None:
    rep = assess(_load("non_causal_const_dk"))
    assert rep.cqm < QualityCriteria().cqm_min
    assert rep.verdict == "warn"
    causal = assess(line_network(np.arange(0.0, 20e9 + 1, 100e6), df=0.2))
    assert causal.cqm > rep.cqm + 30.0


def test_missing_dc_rules() -> None:
    net = _load("starts_at_10mhz")
    rep = assess(net, QualityCriteria(require_dc="present"))
    assert rep.dc == "extrapolated" and rep.verdict == "fail"
    late = skrf.Network(
        frequency=skrf.Frequency.from_f(np.linspace(5e9, 20e9, 40), unit="hz"),
        s=net.s[:40],
        z0=50.0,
    )
    assert assess(late).dc == "missing" and assess(late).verdict == "fail"
    assert assess(late, QualityCriteria(require_dc="none")).verdict != "fail"


def test_enforce_passivity_fixes_violation_with_small_change() -> None:
    bad = _load("passivity_violation")
    good, model = enforce_passivity(bad)
    assert model.passive
    assert assess(good).max_singular_value <= 1.0 + 1e-3
    k = bad.f <= 0.9 * bad.f[-1]
    db = 20.0 * np.log10(np.abs(good.s[k, 1, 0]) / np.abs(bad.s[k, 1, 0]))
    assert np.max(np.abs(db)) <= 0.2


def test_gate_fix_reports_fixes_and_is_fast() -> None:
    bad = _load("passivity_violation")
    t0 = time.perf_counter()
    fixed, rep = gate(bad, QualityCriteria(), fix=True)
    elapsed = time.perf_counter() - t0
    assert elapsed < 5.0
    assert rep.verdict == "warn"
    assert any(x.startswith("passivity_enforced(vf") for x in rep.fixes_applied)
    assert fixed.nports == 2
    _, plain = gate(bad, QualityCriteria())
    assert plain.verdict == "fail" and plain.fixes_applied == ()
    t0 = time.perf_counter()
    assess(_load("passive_line"))
    assert time.perf_counter() - t0 < 1.0


def test_gate_dc_fix_and_make_dc() -> None:
    net = _load("starts_at_10mhz")
    fixed, rep = gate(net, QualityCriteria(require_dc="present"), fix=True)
    assert "dc_extrapolated" in rep.fixes_applied
    assert rep.dc == "present" and fixed.f[0] == 0.0
    assert abs(fixed.s[0, 1, 0] - 0.96) < 0.01
    rat = make_dc(net, "rational")
    assert rat.f[0] == 0.0 and abs(rat.s[0, 1, 0] - 0.96) < 0.02


def test_lossless_line_s_matrix_is_exact_and_causal() -> None:
    f = np.arange(0.0, 20e9 + 1, 100e6)
    tau = 200e-12
    s = np.zeros((f.size, 2, 2), dtype=complex)
    s[:, 0, 1] = s[:, 1, 0] = np.exp(-2j * np.pi * f * tau)
    net = skrf.Network(frequency=skrf.Frequency.from_f(f, unit="hz"), s=s, z0=50.0)
    rep = assess(net)
    assert rep.verdict == "pass" and rep.max_singular_value == pytest.approx(1.0)
    assert rep.cqm > 99.0
    back = shift_reference_plane(net, tau / 2.0)  # de-embed tau/2 per port => remove tau
    assert np.allclose(back.s[:, 1, 0], 1.0)
    out = shift_reference_plane(back, tau / 2.0, mode="extend")
    assert np.allclose(out.s, net.s)


def test_renormalize_series_resistor() -> None:
    f = np.array([1e9])
    r = 50.0
    s = np.array([[[r / (r + 100.0), 100.0 / (r + 100.0)], [100.0 / (r + 100.0), r / (r + 100.0)]]])
    net = skrf.Network(frequency=skrf.Frequency.from_f(f, unit="hz"), s=s, z0=50.0)
    n2 = renormalize(net, 25.0)
    assert n2.z0[0, 0].real == 25.0
    expect = r / (r + 2 * 25.0)  # series R = 50 ohm between 25 ohm ports: S11 = R/(R + 2 z0)
    assert n2.s[0, 0, 0].real == pytest.approx(expect, rel=1e-6)


def test_se2gmm_port_order_and_decoupled_pair() -> None:
    f = np.linspace(1e9, 5e9, 5)
    line = line_network(f)
    s = np.zeros((f.size, 4, 4), dtype=complex)
    for lo, hi in ((0, 2), (1, 3)):  # line A: ports 0->2, line B: ports 1->3
        s[:, lo, lo] = s[:, hi, hi] = line.s[:, 0, 0]
        s[:, lo, hi] = s[:, hi, lo] = line.s[:, 0, 1]
    net = skrf.Network(frequency=skrf.Frequency.from_f(f, unit="hz"), s=s, z0=50.0)
    mm = se2gmm(net)  # pair 0 = ports (0, 1) = input pair, pair 1 = ports (2, 3) = output pair
    # modes: d1, d2, c1, c2
    assert np.allclose(mm.s[:, 1, 0], line.s[:, 0, 1])  # Sdd21
    assert np.allclose(mm.s[:, 3, 2], line.s[:, 0, 1])  # Scc21
    assert np.allclose(mm.s[:, 2, 0], 0.0, atol=1e-12)  # Sdc mixing vanishes
    assert np.allclose(mm.z0[0, :].real, [100.0, 100.0, 25.0, 25.0])
    with pytest.raises(ValueError, match="even number"):
        se2gmm(skrf.Network(frequency=net.frequency, s=s[:, :3, :3], z0=50.0))


def test_one_port_and_criteria_defaults() -> None:
    f = np.linspace(0, 10e9, 101)
    one = skrf.Network(
        frequency=skrf.Frequency.from_f(f, unit="hz"),
        s=np.full((f.size, 1, 1), 0.5 + 0j),
        z0=50.0,
    )
    rep = assess(one)
    assert rep.rqm == 100.0 and rep.verdict == "pass"
    assert Path(__file__).exists()
