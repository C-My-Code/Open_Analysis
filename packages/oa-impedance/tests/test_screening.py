# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Closed-form error bounds against the BEM (WP-516): the calibration table is re-checked on a
hold-out grid (values not used to set the bounds), and the regimes without a bound say so."""

from __future__ import annotations

import itertools

import _xs as X
import pytest

from oa_impedance.errors import ImpedanceUnsupported
from oa_impedance.screening import BOUND_FLAG, MIN_PLANE_MARGIN, REGIMES
from oa_impedance.service import ImpedanceServiceImpl
from oa_model.xsection import CrossSection, XsConductor, XsPlane, XsTrapezoid
from oa_results.container import as_np
from oa_results.rlgc import ImpedanceResult

SVC = ImpedanceServiceImpl(max_workers=1)


def _bound(res: ImpedanceResult) -> float | None:
    for f in res.validity_flags:
        if f.code == BOUND_FLAG:
            return float(f.message.split(";")[0])
    return None


def _z(res: ImpedanceResult, diff: bool) -> float:
    return float(abs(as_np(res.params.z_diff if diff else res.params.z0)[0]))


def _offset(w_over_b: float, h1_over_b: float, er: float, t_over_b: float) -> CrossSection:
    b = 1e-3
    return CrossSection(
        conductors=(
            X.plane("g1", 0.0),
            X.strip("s1", "signal", 0.0, h1_over_b * b, w_over_b * b, t_over_b * b),
            X.plane("g2", b),
        ),
        dielectrics=(X.slab(0.0, b, er),),
        topology_hint="offset_stripline",
        symmetric_about_u0=True,
    )


def _cut_microstrip(u: float, er: float, t_over_h: float, margin: float) -> CrossSection:
    """Plane that ends ``margin * h`` beyond the trace edge on both sides."""
    h = 1e-3
    w, t = u * h, t_over_h * h
    pl = XsConductor(
        id="g1",
        role="ground",
        shape=XsPlane(
            z_bottom=0.0,
            thickness=0.0,
            u_min=-(w / 2 + margin * h),
            u_max=w / 2 + margin * h,
        ),
    )
    return CrossSection(
        conductors=(pl, X.strip("s1", "signal", 0.0, h, w, t)),
        dielectrics=(X.slab(0.0, h, er),),
        topology_hint="microstrip",
        symmetric_about_u0=True,
    )


def _skewed(skew: float) -> CrossSection:
    h, w = 1e-3, 1.5e-3
    tr = XsTrapezoid(u_center=0.0, z_bottom=h, w_bottom=w, w_top=w * (1 - skew), thickness=0.1 * h)
    return CrossSection(
        conductors=(X.plane("g1", 0.0), XsConductor(id="s1", role="signal", shape=tr)),
        dielectrics=(X.slab(0.0, h, 4.3),),
        topology_hint="microstrip",
        symmetric_about_u0=True,
    )


# Hold-out grids: none of these points is on the calibration grid of screening.py's table.
def _cases() -> list[tuple[str, str, CrossSection, bool]]:
    out: list[tuple[str, str, CrossSection, bool]] = []
    for u, th, er in itertools.product((0.3, 0.8, 1.5, 3.0, 5.0), (0.03, 0.15, 0.35), (3.0, 6.5)):
        out.append(("microstrip", f"{u},{th},{er}", X.microstrip(u, er, th), False))
    for u, th, er, cv in itertools.product((0.7, 1.5, 3.0), (0.02, 0.08), (3.6, 5.0), (0.3, 0.8)):
        out.append(
            (
                "embedded_microstrip",
                f"{u},{th},{er},{cv}",
                X.microstrip(u, er, th, cover=cv * 1e-3),
                False,
            )
        )
    for u, th, er in itertools.product((0.15, 0.3, 0.55, 0.9, 1.3), (0.01, 0.07, 0.25), (3.0, 7.0)):
        out.append(("stripline", f"{u},{th},{er}", X.stripline(u, er, th), False))
    for u, h1, th, er in itertools.product(
        (0.15, 0.3, 0.55), (0.25, 0.35, 0.45), (0.02, 0.08), (3.0, 6.0)
    ):
        out.append(("offset_stripline", f"{u},{h1},{th},{er}", _offset(u, h1, er, th), False))
    for u, g, er, th in itertools.product(
        (0.7, 1.5), (0.3, 0.8, 1.5, 3.0), (3.0, 7.0), (0.003, 0.008)
    ):
        out.append(
            (
                "edge_coupled_microstrip",
                f"{u},{g},{er},{th}",
                X.edge_coupled_microstrip(u, g, er, th),
                True,
            )
        )
    for u, s, er, th in itertools.product(
        (0.15, 0.3), (0.15, 0.3, 0.6), (3.0, 7.0), (0.003, 0.008)
    ):
        out.append(
            (
                "edge_coupled_stripline",
                f"{u},{s},{er},{th}",
                X.edge_coupled_stripline(u, s, er, th),
                True,
            )
        )
    for u, er, th in itertools.product((0.6, 2.0), (3.0, 8.0), (1e-6, 0.2)):
        out.append(
            (
                "microstrip",
                f"cut8,{u},{er},{th}",
                _cut_microstrip(u, er, th, MIN_PLANE_MARGIN),
                False,
            )
        )
    out.append(("microstrip", "skew1%", _skewed(0.01), False))
    return out


def test_bound_holds_against_the_bem_on_a_holdout_grid() -> None:
    worst: dict[str, float] = {}
    for family, label, xs, diff in _cases():
        cf = SVC.solve(xs, None, method="closed_form")
        b = _bound(cf)
        assert b is not None, (family, label, cf.validity)
        err = abs(_z(cf, diff) / _z(SVC.solve(xs, None, method="bem"), diff) - 1)
        assert err <= 0.75 * b, (family, label, err, b)
        worst[family] = max(worst.get(family, 0.0), err / b)
    assert set(worst) == set(REGIMES)


def test_table_keeps_a_safety_factor_over_the_calibration_maximum() -> None:
    for name, reg in REGIMES.items():
        assert reg.bound >= 1.5 * reg.observed, name
        if reg.bound_extrapolated is not None:
            assert reg.observed_extrapolated is not None
            assert reg.bound_extrapolated >= 1.5 * reg.observed_extrapolated, name


def test_interval_inverts_the_relative_error() -> None:
    """|Z_cf/Z_bem - 1| <= b  =>  Z_bem in [Z_cf/(1+b), Z_cf/(1-b)] (what IM-002 uses)."""
    xs = X.microstrip(1.9, 4.3, 0.175, h=0.2e-3)
    cf = SVC.solve(xs, None, method="closed_form")
    b = _bound(cf)
    assert b is not None
    zb = _z(SVC.solve(xs, None, method="bem"), False)
    z = _z(cf, False)
    assert z / (1 + b) <= zb <= z / (1 - b)


@pytest.mark.parametrize(
    ("xs", "why"),
    [
        (X.cpw_halfspace(4.3), "no calibrated regime"),
        (_cut_microstrip(1.0, 4.3, 0.1, 3.0), "plane edge"),
        (_skewed(0.10), "trapezoid"),
        (X.microstrip(9.0, 4.3), "calibration box"),  # w/h = 9
        (X.microstrip(1.0, 20.0), "permittivity"),
        (X.edge_coupled_microstrip(1.0, 0.5, 4.3, 0.2), "calibration box"),  # thick coupled pair
        (X.edge_coupled_stripline(0.1, 0.1, 4.3, 0.1), "calibration box"),
    ],
)
def test_no_bound_outside_the_calibrated_regimes(xs: CrossSection, why: str) -> None:
    try:
        cf = SVC.solve(xs, None, method="closed_form")
    except ImpedanceUnsupported:  # a section without any formula has no bound either
        return
    assert _bound(cf) is None, why


def test_no_bound_for_dispersive_frequencies() -> None:
    xs = X.microstrip(1.9, 4.3, 0.1)
    assert _bound(SVC.solve(xs, [0.0], method="closed_form")) is not None
    assert _bound(SVC.solve(xs, [1e9], method="closed_form")) is None
    assert _bound(SVC.solve(xs, [0.0, 1e9], method="closed_form")) is None


def test_bound_flag_is_ok_and_result_stays_screening() -> None:
    cf = SVC.solve(X.microstrip(1.9, 4.3, 0.1), None, method="closed_form")
    assert cf.accuracy_class == "screening"
    flag = next(f for f in cf.validity_flags if f.code == BOUND_FLAG)
    assert flag.ok and "calibrated" in flag.message
