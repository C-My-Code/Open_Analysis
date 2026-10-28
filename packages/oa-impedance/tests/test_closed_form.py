# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Closed forms against the golden suite ``impedance_closedform`` and the research report."""

from __future__ import annotations

import math

import pytest

from oa_impedance import closed_form as cf
from oa_testkit import analytic as an
from oa_testkit.golden import assert_close_to_golden, load_case

SUITE = "impedance_closedform"
H = 0.2e-3


def _key(u: float, er: float) -> str:
    return f"u{u:g}_er{er:g}".replace(".", "p")


@pytest.mark.golden
def test_hj_wheeler_table_locked_and_matches_report() -> None:
    case = load_case(SUITE, "microstrip_hj_wheeler_table")
    actual = {}
    for u, er in case.inputs["rows"]:
        hj = cf.microstrip_hj(u * H, H, 0.0, er)
        wh = cf.microstrip_wheeler(u * H, H, 0.0, er)
        actual[_key(u, er)] = {"hj_z0": hj.z0, "hj_eeff": hj.eeff, "wheeler_z0": wh.z0}
    assert_close_to_golden(actual, SUITE, "microstrip_hj_wheeler_table")
    # independent: the printed values of docs/research/02 sec. 4.3 (3 decimals)
    report = {
        (0.5, 1.0): (166.700, 1.0000, 166.641),
        (0.5, 4.3): (96.404, 2.9901, 96.400),
        (1.0, 4.3): (71.751, 3.1045, 71.732),
        (1.9, 4.3): (50.739, 3.2612, None),
        (2.0, 4.3): (49.192, 3.2755, 49.064),
        (2.0, 10.2): (32.936, 7.3067, 32.961),
        (4.0, 4.3): (30.911, 3.4869, 30.708),
    }
    for (u, er), (z, e, wz) in report.items():
        a = actual[_key(u, er)]
        assert a["hj_z0"] == pytest.approx(z, abs=1.5e-3)
        assert a["hj_eeff"] == pytest.approx(e, abs=1.5e-4)
        if wz is not None:
            assert a["wheeler_z0"] == pytest.approx(wz, abs=1.5e-3)


@pytest.mark.golden
def test_cohn_stripline_report_values() -> None:
    case = load_case(SUITE, "stripline_cohn_er1")
    z = [cf.cohn_z0(w, case.inputs["er"]) for w in case.inputs["w_over_b"]]
    assert_close_to_golden({"z0": z}, SUITE, "stripline_cohn_er1")
    # and the stripline() wrapper at t = 0
    for w, e in zip(case.inputs["w_over_b"], case.expected["z0"], strict=True):
        assert cf.stripline(w * 1e-3, 1e-3, 0.0, 1.0).z0 == pytest.approx(e, abs=0.01)


@pytest.mark.golden
def test_edge_coupled_stripline_report_values() -> None:
    case = load_case(SUITE, "stripline_edge_coupled_cohn")
    r = [
        cf.edge_coupled_stripline(0.5e-3, s * 1e-3, 1e-3, 0.0, 1.0) for s in case.inputs["s_over_b"]
    ]
    assert_close_to_golden(
        {"z0e": [x.z0e for x in r], "z0o": [x.z0o for x in r]}, SUITE, "stripline_edge_coupled_cohn"
    )
    # S -> infinity: both modes converge to the single line (self-consistency)
    far = cf.edge_coupled_stripline(0.5e-3, 50e-3, 1e-3, 0.0, 1.0)
    assert far.z0e == pytest.approx(far.z0o, rel=1e-6)
    assert far.z0e == pytest.approx(cf.cohn_z0(0.5), rel=1e-6)


@pytest.mark.golden
def test_cpw_report_values_and_hilberg() -> None:
    case = load_case(SUITE, "cpw_halfspace")
    z = [cf.cpw(1.0, 0.5, 0.0, er).z0 for er in case.inputs["er"]]
    assert_close_to_golden({"z0": z}, SUITE, "cpw_halfspace")
    for er in case.inputs["er"]:
        assert cf.cpw(1.0, 0.5, 0.0, er).eeff == pytest.approx((er + 1) / 2)
    # Hilberg cross-check of the elliptic ratio (about 3 ppm)
    for k in (0.05, 0.3, 0.5, 0.7, 0.9, 0.99):
        assert cf.hilberg_kratio(k) == pytest.approx(cf.kratio(k), rel=2e-5)


@pytest.mark.golden
def test_ipc_legacy_is_labelled_and_matches_report() -> None:
    case = load_case(SUITE, "ipc2141_legacy_vs_wheeler")
    i = case.inputs
    ipc = cf.ipc2141_microstrip(i["w"], i["h"], i["t"], i["er"])
    wh = cf.microstrip_wheeler(i["w"], i["h"], i["t"], i["er"])
    assert_close_to_golden(
        {"ipc_z0": ipc.z0, "wheeler_z0": wh.z0}, SUITE, "ipc2141_legacy_vs_wheeler"
    )
    assert ipc.legacy and any("legacy" in n for n in ipc.notes)
    assert not wh.legacy


def test_ipc_outside_validity_is_flagged() -> None:
    r = cf.ipc2141_microstrip(1.0e-3, 0.2e-3, 35e-6, 4.3)  # W/h = 5
    assert r.validity == "extrapolated"
    assert any("W/h" in v for v in r.violations)


def test_validity_flags_every_family() -> None:
    assert cf.microstrip_hj(0.2e-3, 0.2e-3, 0.0, 4.3).validity == "in_range"
    assert cf.microstrip_hj(0.2e-3, 0.2e-3, 0.0, 4.3, 150e9).validity != "in_range"  # f*h = 30
    assert cf.microstrip_hj(1e-3, 0.2e-3, 0.0, 500.0).validity == "invalid"
    assert cf.stripline(0.5e-3, 0.5e-3, 0.2e-3, 4.3).validity == "extrapolated"  # t/b = 0.4
    assert cf.cpw(1e-3, 0.5e-3, 0.0, 4.3, 0.3e-3).validity != "in_range"  # h/(S+2G) = 0.15
    assert cf.edge_coupled_microstrip(0.2e-3, 0.01e-3, 0.2e-3, 0.0, 4.3).validity == "extrapolated"


def test_offset_stripline_reduces_to_symmetric() -> None:
    b, t, w = 1e-3, 35e-6, 0.3e-3
    h = (b - t) / 2
    off = cf.offset_stripline(w, h, h, t, 4.0)
    sym = cf.stripline(w, b, t, 4.0)
    assert off.z0 == pytest.approx(sym.z0, rel=1e-9)


def test_embedded_cover_limits() -> None:
    # no cover -> surface microstrip; deep cover -> homogeneous (eeff -> er)
    ms = cf.microstrip_hj(0.2e-3, 0.2e-3, 0.0, 4.3)
    assert cf.embedded_microstrip(0.2e-3, 0.2e-3, 0.0, 4.3, 0.0).eeff == pytest.approx(ms.eeff)
    deep = cf.embedded_microstrip(0.2e-3, 0.2e-3, 0.0, 4.3, 5e-3)
    assert deep.eeff == pytest.approx(4.3, rel=1e-3)
    assert deep.validity != "in_range"


def test_dispersion_raises_eeff_toward_er() -> None:
    lo = cf.microstrip_hj(0.35e-3, 0.2e-3, 35e-6, 4.3, 0.0)
    hi = cf.microstrip_hj(0.35e-3, 0.2e-3, 35e-6, 4.3, 20e9)
    assert lo.eeff is not None and hi.eeff is not None and lo.eeff < hi.eeff < 4.3


def test_coupled_microstrip_limits() -> None:
    # wide gap: both modes converge to the single line
    r = cf.edge_coupled_microstrip(1.0e-3, 10e-3, 1e-3, 0.0, 4.3)
    assert r.z0e == pytest.approx(r.z0, rel=0.01)
    assert r.z0o == pytest.approx(r.z0, rel=0.01)
    close = cf.edge_coupled_microstrip(1.0e-3, 0.1e-3, 1e-3, 0.0, 4.3)
    assert close.z0 is not None and close.z0o is not None and close.z0e is not None
    assert close.eeff_o is not None and close.eeff_e is not None
    assert close.z0o < close.z0 < close.z0e and close.eeff_o < close.eeff_e


def test_wheeler_loss_rule_orders_of_magnitude() -> None:
    def z_air(w: float, h: float, t: float) -> float:
        r = cf.microstrip_hj(w, h, t, 1.0)
        assert r.z0 is not None
        return r.z0

    r = cf.microstrip_hj(0.35e-3, 0.2e-3, 35e-6, 4.3)
    assert r.z0 is not None
    a, valid = cf.wheeler_conductor_loss_np_per_m(z_air, 0.35e-3, 0.2e-3, 35e-6, r.z0, 1e9)
    db_per_inch = a * 8.686 * 0.0254
    assert valid
    assert 0.02 < db_per_inch < 0.2  # ~0.05-0.1 dB/in for a 50 ohm 1 oz trace at 1 GHz


def test_wire_formulas_exact_helpers_agree() -> None:
    assert an.wire_over_plane_z0(2.0) == pytest.approx(
        an.ETA0 / (2 * math.pi) * math.acosh(2.0), rel=1e-12
    )
