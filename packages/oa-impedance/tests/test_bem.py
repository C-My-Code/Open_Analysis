# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""2D BEM against exact solutions (golden suite ``impedance_bem``) and structural identities."""

from __future__ import annotations

import math
from itertools import pairwise

import _xs
import numpy as np
import pytest

from oa_impedance import _physics_compat as phys
from oa_impedance import closed_form as cf
from oa_impedance.bem import MeshParams, solve_bem
from oa_impedance.service import ImpedanceServiceImpl
from oa_model.materials import DjordjevicSarkar
from oa_model.xsection import CrossSection, XsDielectric, XsSlab
from oa_testkit import xsections as tk
from oa_testkit.golden import assert_close_to_golden, load_case

SUITE = "impedance_bem"


def _svc() -> ImpedanceServiceImpl:
    return ImpedanceServiceImpl()


def z0_of(xs: CrossSection, mesh: MeshParams | None = None) -> float:
    r = ImpedanceServiceImpl(mesh=mesh).solve(xs, [0.0], method="bem")
    assert r.params.z0 is not None
    return float(r.params.z0.array[0])


def eeff_of(xs: CrossSection) -> float:
    r = _svc().solve(xs, [0.0], method="bem")
    return float(r.params.eps_eff_modal.array[0, 0])


def mixed_of(xs: CrossSection) -> tuple[float, float]:
    r = _svc().solve(xs, [0.0], method="bem")
    assert r.params.z_diff is not None and r.params.z_comm is not None
    return float(r.params.z_diff.array[0]), float(r.params.z_comm.array[0])


# ----------------------------------------------------------------------------- exact solutions


@pytest.mark.golden
def test_coax_exact() -> None:
    case = load_case(SUITE, "coax_lnba1")
    z = [z0_of(tk.get_case("coax_lnba1_air").xs), z0_of(tk.get_case("coax_lnba1_er2p1").xs)]
    assert_close_to_golden({"z0": z}, SUITE, "coax_lnba1")
    assert case.expected["z0"][0] == pytest.approx(59.958, abs=1e-3)


@pytest.mark.golden
def test_wire_over_plane_exact() -> None:
    z = [z0_of(tk.get_case(f"wire_over_plane_h{h}r").xs) for h in ("2", "5")]
    assert_close_to_golden({"z0": z}, SUITE, "wire_over_plane")


@pytest.mark.golden
def test_two_wire_exact() -> None:
    r = _svc().solve(tk.get_case("two_wire_D2p5d").xs, [0.0], method="bem")
    assert r.params.z0 is not None  # no ground conductor: one mutual mode = the line impedance
    assert_close_to_golden({"zdiff": float(r.params.z0.array[0])}, SUITE, "two_wire")


@pytest.mark.golden
def test_true_thin_strip_cohn_stripline() -> None:
    case = load_case(SUITE, "cohn_thin_stripline")
    assert case.inputs["t_over_b"] <= 1e-6
    actual = {
        f"z0_er{str(er).replace('.', 'p').replace('p0', '')}": [
            z0_of(_xs.stripline(w, er, case.inputs["t_over_b"])) for w in case.inputs["w_over_b"]
        ]
        for er in (1.0, 4.3)
    }
    assert_close_to_golden(actual, SUITE, "cohn_thin_stripline")


@pytest.mark.golden
def test_true_thin_strip_edge_coupled_stripline() -> None:
    case = load_case(SUITE, "cohn_thin_edge_coupled")
    zd, zc = [], []
    for s in case.inputs["s_over_b"]:
        a, b = mixed_of(_xs.edge_coupled_stripline(0.5, s, 1.0, case.inputs["t_over_b"]))
        zd.append(a)
        zc.append(b)
    assert_close_to_golden({"zdiff": zd, "zcomm": zc}, SUITE, "cohn_thin_edge_coupled")


@pytest.mark.golden
def test_cpw_on_half_space() -> None:
    case = load_case(SUITE, "cpw_halfspace")
    ee, zz = [], []
    for er in case.inputs["er"]:
        xs = _xs.cpw_halfspace(er, depth=case.inputs["depth"])
        r = _svc().solve(xs, [0.0], method="bem")
        assert r.params.z0 is not None
        ee.append(float(r.params.eps_eff_modal.array[0, 0]))
        zz.append(float(r.params.z0.array[0]))
    assert_close_to_golden({"eeff_halfspace": ee, "z0": zz}, SUITE, "cpw_halfspace")


# ----------------------------------------------------------------------------- convergence


def test_panel_convergence_error_halves_per_doubling() -> None:
    """Thin-strip Cohn stripline vs the exact value; refinement factor 1 -> 2 -> 4."""
    xs = _xs.stripline(0.5, 1.0)
    exact = 100.43245078474767
    errs = [abs(z0_of(xs, MeshParams().scaled(f)) / exact - 1) for f in (0.5, 1.0, 2.0, 4.0)]
    # documented: error roughly quarters per doubling (second order) until the 1e-4 floor of
    # the 20*b truncation and the polygon/plane approximations
    for coarse, fine in pairwise(errs):
        assert fine <= max(coarse / 2.0, 2e-4), errs
    assert errs[-1] < 3e-4


def test_convergence_microstrip_richardson() -> None:
    xs = _xs.microstrip(1.0, 4.3, t_over_h=0.05)
    z = [z0_of(xs, MeshParams().scaled(f)) for f in (1.0, 2.0, 4.0)]
    d1, d2 = abs(z[1] - z[0]), abs(z[2] - z[1])
    assert d2 < d1 / 1.8  # successive differences shrink
    assert d2 / z[2] < 1e-3


# ----------------------------------------------------------------------------- identities


def test_homogeneous_lc_identity_and_symmetry() -> None:
    er = 3.0
    xs = _xs.homogeneous_pair(er)
    s = solve_bem(xs, [0.0])
    L, C = s.L_ext, s.C[0].real
    np.testing.assert_allclose(
        L @ C, phys.MU0 * phys.EPS0 * er * np.eye(2), rtol=1e-6, atol=1e-12 * phys.MU0 * phys.EPS0
    )
    assert np.max(np.abs(C - C.T)) / np.max(np.abs(C)) < 1e-9
    assert abs(C[0, 0] - C[1, 1]) / C[0, 0] < 1e-9  # mirror symmetry
    assert C[0, 1] < 0 and C.sum(axis=1).min() >= 0  # Maxwell form, row sums >= 0
    np.testing.assert_allclose(s.C0, C / er, rtol=1e-6)


def test_mixed_mode_matches_even_odd_for_symmetric_pair() -> None:
    xs = _xs.pcb_microstrip(pair=True, df=0.0)
    r = _svc().solve(xs, [0.0], method="bem")
    p = r.params
    assert p.z_diff is not None and p.z_odd is not None and p.z_even is not None
    assert p.z_comm is not None
    assert float(p.z_diff.array[0]) == pytest.approx(2 * float(p.z_odd.array[0]))
    assert float(p.z_comm.array[0]) == pytest.approx(float(p.z_even.array[0]) / 2)
    zm = p.zc_modal.array[0].real
    assert zm[0] == pytest.approx(float(p.z_even.array[0]), rel=1e-6)  # modal order [even, odd]
    assert zm[1] == pytest.approx(float(p.z_odd.array[0]), rel=1e-6)
    ee = p.eps_eff_modal.array[0]
    assert ee[0] > ee[1]  # even mode has the larger eps_eff for edge-coupled microstrip
    # L, C based formulas of research sec. 3.4
    rl = r.rlgc
    assert rl is not None
    L, C = rl.L.array[0], rl.C.array[0]
    zodd = math.sqrt((L[0, 0] - L[0, 1]) / (C[0, 0] - C[0, 1]))
    assert zodd == pytest.approx(float(p.z_odd.array[0]), rel=0.03)  # exact only if homogeneous


# ----------------------------------------------------------------------------- vs closed forms


@pytest.mark.parametrize(
    ("u", "er"), [(0.5, 4.3), (1.0, 4.3), (2.0, 4.3), (2.0, 10.2), (4.0, 4.3), (1.0, 2.2)]
)
def test_bem_microstrip_vs_hammerstad_jensen(u: float, er: float) -> None:
    xs = _xs.microstrip(u, er)
    hj = cf.microstrip_hj(u * 1e-3, 1e-3, 0.0, er)
    assert hj.validity == "in_range"
    assert z0_of(xs) == pytest.approx(hj.z0, rel=0.01)
    assert eeff_of(xs) == pytest.approx(hj.eeff, rel=0.01)


@pytest.mark.parametrize(("u", "er", "th"), [(1.0, 4.3, 0.05), (2.0, 4.3, 0.035), (0.5, 3.0, 0.1)])
def test_bem_thick_microstrip_vs_hj_thickness(u: float, er: float, th: float) -> None:
    xs = _xs.microstrip(u, er, t_over_h=th)
    hj = cf.microstrip_hj(u * 1e-3, 1e-3, th * 1e-3, er)
    assert z0_of(xs) == pytest.approx(hj.z0, rel=0.015)


@pytest.mark.parametrize(("w", "th"), [(0.5, 0.02), (0.5, 0.1), (1.0, 0.05), (0.25, 0.05)])
def test_bem_thick_stripline_vs_wheeler(w: float, th: float) -> None:
    xs = _xs.stripline(w, 1.0, th)
    ref = cf.stripline(w * 1e-3, 1e-3, th * 1e-3, 1.0)
    assert ref.validity == "in_range"
    assert z0_of(xs) == pytest.approx(ref.z0, rel=0.01)


@pytest.mark.parametrize(("u", "g", "er"), [(1.0, 0.5, 4.3), (0.5, 1.0, 9.8), (2.0, 1.0, 3.5)])
def test_bem_coupled_microstrip_vs_kirschning_jansen(u: float, g: float, er: float) -> None:
    xs = _xs.edge_coupled_microstrip(u, g, er)
    kj = cf.edge_coupled_microstrip(u * 1e-3, g * 1e-3, 1e-3, 0.0, er)
    assert kj.validity == "in_range"
    r = _svc().solve(xs, [0.0], method="bem")
    p = r.params
    assert p.z_even is not None and p.z_odd is not None
    assert float(p.z_even.array[0]) == pytest.approx(kj.z0e, rel=0.01)
    assert float(p.z_odd.array[0]) == pytest.approx(kj.z0o, rel=0.01)
    ee = p.eps_eff_modal.array[0]
    assert ee[0] == pytest.approx(kj.eeff_e, rel=0.01)
    assert ee[1] == pytest.approx(kj.eeff_o, rel=0.015)


def test_bem_edge_coupled_stripline_thick_vs_cohn_wadell() -> None:
    xs = _xs.edge_coupled_stripline(0.5, 0.25, 1.0, t_over_b=0.05)
    ref = cf.edge_coupled_stripline(0.5e-3, 0.25e-3, 1e-3, 0.05e-3, 1.0)
    p = _svc().solve(xs, [0.0], method="bem").params
    assert p.z_even is not None and p.z_odd is not None
    assert float(p.z_even.array[0]) == pytest.approx(ref.z0e, rel=0.025)
    assert float(p.z_odd.array[0]) == pytest.approx(ref.z0o, rel=0.025)


def test_bem_cpwg_vs_conformal_mapping() -> None:
    from _xs import plane, slab, strip

    s, g, h = 0.3e-3, 0.15e-3, 0.3e-3
    t = 1e-6 * s
    half = s / 2 + g
    xs = CrossSection(
        conductors=(
            plane("bot", 0.0),
            strip("s1", "signal", 0.0, h, s, t),
            plane("g1", h, t, u_max=-half),
            plane("g2", h, t, u_min=+half),
        ),
        dielectrics=(slab(0.0, h, 4.3),),
        topology_hint="cpwg",
        symmetric_about_u0=True,
    )
    ref = cf.cpw(s, g, 0.0, 4.3, h, backside=True)
    assert z0_of(xs) == pytest.approx(ref.z0, rel=0.01)


# ----------------------------------------------------------------------------- frequency dependence


def test_ds_dielectric_interpolation_matches_direct_solve() -> None:
    model = DjordjevicSarkar(dk=4.3, df=0.02, f_ref=1e9, f_low=1e3, f_high=1e12)
    xs = _xs.stripline(0.4, 4.3)
    xs = xs.model_copy(
        update={
            "dielectrics": (XsDielectric(region=XsSlab(z_bottom=0.0, z_top=1e-3), model=model),)
        }
    )
    grid = list(np.logspace(6, 10, 9))
    full = solve_bem(xs, [float(g) for g in grid])
    assert full.n_solves < len(grid)  # anchored + interpolated
    probe = solve_bem(xs, [float(grid[3])])
    np.testing.assert_allclose(full.C[3], probe.C[0], rtol=2e-4)
    # causal DS: Dk falls and loss tangent stays finite across the band
    assert full.C[0, 0, 0].real > full.C[-1, 0, 0].real > 0
    assert (-full.C[:, 0, 0].imag > 0).all()


def test_conductor_loss_matches_coax_skin_effect() -> None:
    xs = tk.get_case("coax_lnba1_air").xs
    f = 1e9
    s = solve_bem(xs, [f])
    sigma = 5.8e7
    a, b = 0.5e-3, 0.5e-3 * math.e
    r_exact = phys.surface_resistance(f, sigma) / (2 * math.pi) * (1 / a + 1 / b)
    assert float(s.R_ac[0, 0, 0]) == pytest.approx(r_exact, rel=0.01)
    s2 = solve_bem(xs, [4e9])
    assert float(s2.R_ac[0, 0, 0]) / float(s.R_ac[0, 0, 0]) == pytest.approx(
        2.0, rel=1e-6
    )  # sqrt(f)


def test_roughness_raises_resistance() -> None:
    from oa_model.materials import Hammerstad

    xs = _xs.stripline(0.5, 4.3, t_over_b=0.035)
    rough = xs.model_copy(
        update={
            "conductors": tuple(
                c.model_copy(
                    update={
                        "roughness_top": Hammerstad(rq=2e-6),
                        "roughness_bottom": Hammerstad(rq=2e-6),
                    }
                )
                if c.role == "signal"
                else c
                for c in xs.conductors
            )
        }
    )
    f = 10e9
    r0 = float(solve_bem(xs, [f]).R_ac[0, 0, 0])
    r1 = float(solve_bem(rough, [f]).R_ac[0, 0, 0])
    assert 1.2 * r0 < r1 < 2.0 * r0


def test_unused_dielectric_background_is_air() -> None:
    xs = _xs.stripline(0.5, 1.0)
    assert eeff_of(xs) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize(
    "case", [c for c in tk.all_cases() if c.kind == "analytic"], ids=lambda c: c.name
)
def test_bem_meets_testkit_exact_cases_at_their_own_rtol(case: tk.XsCase) -> None:
    # testkit asks 1e-4 for round conductors; flat panels give ~1e-3 there, 3e-3 is the WP target
    rtol = max(case.rtol, 3e-3) if case.topology != "coax" else case.rtol
    r = _svc().solve(case.xs, [0.0], method="bem")
    p = r.params
    for key, exp in case.expected.items():
        if key == "z0":
            assert p.z0 is not None
            assert float(p.z0.array[0]) == pytest.approx(exp, rel=rtol)
        elif key == "zdiff":
            got = p.z_diff if p.z_diff is not None else p.z0
            assert got is not None
            assert float(got.array[0]) == pytest.approx(exp, rel=rtol)
        elif key == "eeff":
            assert float(p.eps_eff_modal.array[0, 0]) == pytest.approx(exp, rel=rtol)
