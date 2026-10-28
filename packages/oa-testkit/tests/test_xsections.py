# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""CrossSection fixtures: canonical form, provenance of expected values, key stability."""

from __future__ import annotations

import math

import pytest

from oa_model.xsection import (
    CrossSection,
    XsCircle,
    XsPlane,
    XsPolygon,
    XsSlab,
    XsTrapezoid,
)
from oa_testkit import analytic as an
from oa_testkit.xsections import XsCase, all_cases, get_case

CASES = all_cases()


def _ids(cases: tuple[XsCase, ...]) -> list[str]:
    return [c.name for c in cases]


def test_case_inventory() -> None:
    names = {c.name for c in CASES}
    for required in (
        "hj_microstrip_u0p5_er1",
        "hj_microstrip_u4_er4p3",
        "cohn_stripline_w0p1_er1",
        "cohn_stripline_w2_er1",
        "edge_coupled_stripline_w0p5_s0p1_er1",
        "cpw_s1_g0p5_air",
        "coax_lnba1_air",
        "wire_over_plane_h2r",
        "two_wire_D2p5d",
    ):
        assert required in names
    assert sum(c.name.startswith("hj_") for c in CASES) == 7  # the research table rows
    assert sum(c.name.startswith("cohn_") for c in CASES) >= 5


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_case_metadata(case: XsCase) -> None:
    assert case.source.strip()
    assert case.expected
    assert all(math.isfinite(v) and v > 0 for v in case.expected.values())
    assert 0 < case.rtol < 0.05
    if case.kind == "formula_lock":
        assert "self-computed, needs independent check" in case.source.lower()
    else:
        assert "self-computed" not in case.source.lower()
        assert case.source.startswith("analytic:")


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_json_round_trip_and_key(case: XsCase) -> None:
    xs = case.xs
    again = CrossSection.model_validate_json(xs.model_dump_json())
    assert again == xs
    assert again.key() == xs.key()
    assert xs.key().startswith("xs1:")


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_already_canonical(case: XsCase) -> None:
    assert CrossSection.canonicalize(case.xs).key() == case.xs.key()


def _signal_u_extent(xs: CrossSection) -> tuple[float, float]:
    lo, hi = math.inf, -math.inf
    for c in xs.conductors:
        if c.role != "signal":
            continue
        s = c.shape
        if isinstance(s, XsTrapezoid):
            lo, hi = min(lo, s.u_center - s.w_bottom / 2), max(hi, s.u_center + s.w_bottom / 2)
        elif isinstance(s, XsCircle):
            lo, hi = min(lo, s.u_center - s.radius), max(hi, s.u_center + s.radius)
    return lo, hi


def _lowest_z(xs: CrossSection) -> float:
    zs: list[float] = []
    for c in xs.conductors:
        s = c.shape
        if isinstance(s, (XsTrapezoid, XsPlane)):
            zs.append(s.z_bottom)
        elif isinstance(s, XsCircle):
            zs.append(s.z_center - s.radius)
        elif isinstance(s, XsPolygon):
            zs.append(float(s.polygon.exterior.array[:, 1].min()))
    for d in xs.dielectrics:
        if isinstance(d.region, XsSlab):
            zs.append(d.region.z_bottom)
    return min(zs)


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_canonical_frame(case: XsCase) -> None:
    lo, hi = _signal_u_extent(case.xs)
    assert (lo + hi) / 2 == pytest.approx(0.0, abs=1e-12)
    assert _lowest_z(case.xs) == pytest.approx(0.0, abs=1e-12)


def test_signal_ids_order() -> None:
    assert get_case("edge_coupled_stripline_w0p5_s0p25_er1").xs.signal_ids() == ("s1", "s2")
    assert get_case("coax_lnba1_air").xs.signal_ids() == ("s1",)


def test_expected_values_match_formulas() -> None:
    assert get_case("coax_lnba1_air").expected["z0"] == pytest.approx(59.958, abs=5e-4)
    assert get_case("cohn_stripline_w0p5_er1").expected["z0"] == pytest.approx(100.43, abs=6e-3)
    assert get_case("cpw_s1_g0p5_air").expected["z0"] == pytest.approx(120.57, abs=6e-3)
    assert get_case("hj_microstrip_u1p9_er4p3").expected["z0"] == pytest.approx(50.739, abs=6e-4)
    e = get_case("edge_coupled_stripline_w0p5_s0p1_er1").expected
    assert (e["z0e"], e["z0o"]) == pytest.approx((122.89, 69.87), abs=6e-3)
    assert e["zdiff"] == pytest.approx(2 * e["z0o"])
    wire = get_case("wire_over_plane_h2r").expected["z0"]
    assert wire == pytest.approx(an.ETA0 / (2 * math.pi) * math.acosh(2.0))
    two = get_case("two_wire_D2p5d").expected["zdiff"]
    assert two == pytest.approx(an.ETA0 / math.pi * math.acosh(2.5))


def test_er_scaling_of_homogeneous_cases() -> None:
    z1 = get_case("cohn_stripline_w0p5_er1").expected["z0"]
    z43 = get_case("cohn_stripline_w0p5_er4p3").expected["z0"]
    assert z43 == pytest.approx(z1 / math.sqrt(4.3), rel=1e-12)


def test_key_sensitivity() -> None:
    """key() sees a 2 nm move (the sub-nm jitter contract is tested in oa_model)."""
    xs = get_case("hj_microstrip_u2_er4p3").xs
    sig = xs.conductors[0]
    assert isinstance(sig.shape, XsTrapezoid)

    def moved(du: float) -> CrossSection:
        shape = sig.shape.model_copy(update={"u_center": sig.shape.u_center + du})  # type: ignore[union-attr]
        c0 = sig.model_copy(update={"shape": shape})
        return xs.model_copy(update={"conductors": (c0, *xs.conductors[1:])})

    assert moved(2e-9).key() != xs.key()
