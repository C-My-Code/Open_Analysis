# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""FEM electrostatics vs exact solutions (golden impedance_fem), BEM and identities."""

from __future__ import annotations

import _xs
import numpy as np
import pytest

from oa_impedance import methods
from oa_impedance.bem import MeshParams
from oa_impedance.errors import MethodNotAvailable
from oa_impedance.fem import FemParams, solve_fem
from oa_impedance.service import ImpedanceServiceImpl
from oa_model.xsection import CrossSection
from oa_physics.constants import EPS0, MU0
from oa_testkit import xsections as tk
from oa_testkit.golden import assert_close_to_golden, load_case

SUITE = "impedance_fem"
NO_LOSS = FemParams(loss="none")


def fem(xs: CrossSection, freqs: list[float] | None = None):  # type: ignore[no-untyped-def]
    return solve_fem(xs, freqs or [0.0], NO_LOSS)


def z0_of(xs: CrossSection) -> float:
    r = fem(xs)
    assert r.params.z0 is not None
    return float(r.params.z0.array[0])


@pytest.mark.slow  # nightly: heavy reference case (CI time, 2026-10-31)
@pytest.mark.golden
def test_coax_exact() -> None:
    z = [z0_of(tk.get_case("coax_lnba1_air").xs), z0_of(tk.get_case("coax_lnba1_er2p1").xs)]
    assert_close_to_golden({"z0": z}, SUITE, "coax_lnba1")


@pytest.mark.slow  # nightly: heavy reference case (CI time, 2026-10-31)
@pytest.mark.golden
def test_wire_over_plane_exact() -> None:
    z = [z0_of(tk.get_case(f"wire_over_plane_h{h}r").xs) for h in ("2", "5")]
    assert_close_to_golden({"z0": z}, SUITE, "wire_over_plane")


@pytest.mark.slow  # nightly: heavy reference case (CI time, 2026-10-31)
@pytest.mark.golden
def test_two_wire_exact() -> None:
    r = fem(tk.get_case("two_wire_D2p5d").xs)
    assert r.params.z0 is not None
    assert_close_to_golden({"zdiff": float(r.params.z0.array[0])}, SUITE, "two_wire")


@pytest.mark.slow  # nightly: heavy reference case (CI time, 2026-10-31)
@pytest.mark.golden
def test_cohn_thin_stripline() -> None:
    case = load_case(SUITE, "cohn_thin_stripline")
    actual = {
        f"z0_er{str(er).replace('.', 'p').replace('p0', '')}": [
            z0_of(_xs.stripline(w, er, case.inputs["t_over_b"])) for w in case.inputs["w_over_b"]
        ]
        for er in (1.0, 4.3)
    }
    assert_close_to_golden(actual, SUITE, "cohn_thin_stripline")


@pytest.mark.golden
def test_cohn_thin_edge_coupled() -> None:
    case = load_case(SUITE, "cohn_thin_edge_coupled")
    zd, zc = [], []
    for s in case.inputs["s_over_b"]:
        p = fem(_xs.edge_coupled_stripline(0.5, s, 1.0, case.inputs["t_over_b"])).params
        assert p.z_diff is not None and p.z_comm is not None
        zd.append(float(p.z_diff.array[0]))
        zc.append(float(p.z_comm.array[0]))
    assert_close_to_golden({"zdiff": zd, "zcomm": zc}, SUITE, "cohn_thin_edge_coupled")


@pytest.mark.slow  # nightly: heavy reference case (CI time, 2026-10-31)
@pytest.mark.golden
def test_cpw_on_half_space() -> None:
    case = load_case(SUITE, "cpw_halfspace")
    ee, zz = [], []
    for er in case.inputs["er"]:
        r = fem(_xs.cpw_halfspace(er, depth=case.inputs["depth"]))
        assert r.params.z0 is not None
        ee.append(float(r.params.eps_eff_modal.array[0, 0]))
        zz.append(float(r.params.z0.array[0]))
    assert_close_to_golden({"eeff_halfspace": ee, "z0": zz}, SUITE, "cpw_halfspace")


@pytest.mark.parametrize(
    "xs",
    [
        _xs.microstrip(1.0, 4.3, t_over_h=0.035),
        _xs.pcb_microstrip(pair=True, df=0.0),
        _xs.pcb_microstrip(pair=False, mask=True, df=0.0),
        _xs.edge_coupled_microstrip(1.0, 0.5, 4.3),
    ],
    ids=["microstrip", "pcb_pair", "pcb_masked", "coupled_thin"],
)
def test_fem_agrees_with_bem(xs: CrossSection) -> None:
    f = fem(xs).params
    # the default BEM mesh is ~0.5 % low in eps_eff on 35 um thick strips (converges upwards
    # towards the FEM value: 3.027, 3.043, 3.050, 3.054 for mesh scale 0.5 .. 4), so compare
    # against the BEM at twice the default panel density
    b = ImpedanceServiceImpl(mesh=MeshParams().scaled(2.0)).solve(xs, [0.0], method="bem").params
    key = "z_diff" if b.z_diff is not None else "z0"
    zf, zb = getattr(f, key).array[0], getattr(b, key).array[0]
    assert float(zf) == pytest.approx(float(zb), rel=5e-3)
    np.testing.assert_allclose(f.eps_eff_modal.array[0], b.eps_eff_modal.array[0], rtol=5e-3)


@pytest.mark.slow  # nightly: heavy reference case (CI time, 2026-10-31)
def test_homogeneous_lc_identity_and_symmetry() -> None:
    er = 3.0
    r = fem(_xs.homogeneous_pair(er))
    assert r.rlgc is not None
    L, C = r.rlgc.L.array[0], r.rlgc.C.array[0]
    np.testing.assert_allclose(
        L @ C, MU0 * EPS0 * er * np.eye(2), rtol=1e-6, atol=1e-12 * MU0 * EPS0
    )
    assert np.max(np.abs(C - C.T)) / np.max(np.abs(C)) < 1e-9
    assert abs(C[0, 0] - C[1, 1]) / C[0, 0] < 1e-3  # mirror symmetry (mesh is not mirrored)
    assert C[0, 1] < 0


def test_complex_permittivity_gives_g() -> None:
    xs = _xs.microstrip(1.0, 4.3, df=0.02)
    r = fem(xs, [1e9])
    assert r.rlgc is not None
    c, g = r.rlgc.C.array[0, 0, 0], r.rlgc.G.array[0, 0, 0]
    # filling-factor tan delta: G / (w C) between 0 and df, close to q * df
    tand = g / (2 * np.pi * 1e9 * c)
    assert 0.5 * 0.02 < tand <= 0.02 * 1.0001


def test_method_registered_and_flagged() -> None:
    assert "fem" in methods.names()
    r = ImpedanceServiceImpl().solve(_xs.stripline(0.5, 1.0), [0.0], method="fem")
    assert r.method == "fem"
    assert r.accuracy_class in ("reference", "validated")
    acc = next(f for f in r.validity_flags if f.code == "accuracy")
    assert "accuracy_class=2d_field" in acc.message and "tolerance_basis=golden:" in acc.message
    assert "converged=" in acc.message


def test_unregistered_method_still_raises() -> None:
    with pytest.raises(MethodNotAvailable):
        methods.get("no_such_method")
