# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""WP-519: block elimination on one ``P_cc`` factorisation against the whole-system oracle.

The conductor rows of the BEM system do not depend on the permittivities. ``solve_bem`` factors
that block once and eliminates it (``solver._solve_eps_block``, ``_vacuum_block``); the oracle
(``OA_BEM_BLOCK=0``) factors the whole bordered system per solve, as the code did before. Both are
exact in exact arithmetic, so they agree to the conditioning noise of the system (a few 1e-10 on
the mix sections; the bound used here is 2e-9 of the largest entry).
"""

from __future__ import annotations

import _xs
import impedance_mix_helpers as mix
import numpy as np
import pytest

from oa_impedance.bem import solve_bem
from oa_impedance.bem import solver as bem_solver
from oa_model.materials import DjordjevicSarkar
from oa_model.xsection import CrossSection, XsConductor, XsDielectric, XsSlab, XsTrapezoid

FREQS = [0.0, 1e6, 3e8, 5e9]
TOL = 2e-9


def _both(xs: CrossSection, monkeypatch: pytest.MonkeyPatch, freqs=FREQS):
    monkeypatch.setenv("OA_DEVICE", "cpu")
    monkeypatch.setattr(bem_solver, "_BLOCK", True)
    blk = solve_bem(xs, freqs)
    monkeypatch.setattr(bem_solver, "_BLOCK", False)
    ref = solve_bem(xs, freqs)
    return blk, ref


def _assert_close(a, b) -> None:
    assert a.n_panels == b.n_panels and a.n_solves == b.n_solves
    for name in ("C", "C0", "L_ext", "R_ac", "R_dc"):
        x, y = getattr(a, name), getattr(b, name)
        scale = max(float(np.abs(y).max()), 1e-300)
        assert float(np.abs(x - y).max()) / scale < TOL, name


@pytest.mark.parametrize("seed", range(100, 108))
def test_block_matches_whole_system_on_mix_sections(seed: int, monkeypatch) -> None:
    blk, ref = _both(mix.mix_section(seed), monkeypatch)
    _assert_close(blk, ref)


def test_block_with_ribbons(monkeypatch) -> None:
    """Zero-thickness strip and plane: ribbon panels, whose charge functional uses the D rows."""
    _assert_close(*_both(_xs.stripline(0.6, 3.4), monkeypatch))
    _assert_close(*_both(_xs.edge_coupled_microstrip(1.0, 0.6, 4.3), monkeypatch))


def test_block_with_floating_conductor(monkeypatch) -> None:
    h = 0.2e-3
    xs = CrossSection(
        conductors=(
            _xs.plane("g1", 0.0, 35e-6),
            _xs.strip("s1", "signal", 0.0, h, 0.2e-3, 35e-6),
            XsConductor(
                id="f1",
                role="floating",
                shape=XsTrapezoid(
                    u_center=0.5e-3, z_bottom=h, w_bottom=0.3e-3, w_top=0.3e-3, thickness=35e-6
                ),
            ),
        ),
        dielectrics=(_xs.slab(35e-6, 2 * h, 4.0, 0.01),),
        topology_hint="general",
        symmetric_about_u0=False,
    )
    _assert_close(*_both(xs, monkeypatch))


def test_block_with_dispersive_dielectric(monkeypatch) -> None:
    """Up to six anchors: every one reuses the factor, so only the Schur complement changes."""
    ds = DjordjevicSarkar(dk=4.2, df=0.02)
    base = mix.mix_section(104, n_traces=3, inner=False)
    xs = base.model_copy(
        update={
            "dielectrics": tuple(
                XsDielectric(
                    region=XsSlab(z_bottom=d.region.z_bottom, z_top=d.region.z_top), model=ds
                )
                for d in base.dielectrics
            )
        }
    )
    freqs = list(np.logspace(5, 10, 12))
    blk, ref = _both(xs, monkeypatch, freqs)
    assert blk.n_solves > 2
    _assert_close(blk, ref)


def test_conductor_block_is_factored_once(monkeypatch) -> None:
    """C0 and every anchor of a dispersive sweep use one LU of P_cc."""

    monkeypatch.setenv("OA_DEVICE", "cpu")
    calls: list[int] = []
    real = bem_solver._host_lu_factor

    def counting(a, *args, **kwargs):
        calls.append(a.shape[0])
        return real(a, *args, **kwargs)

    monkeypatch.setattr(bem_solver, "_host_lu_factor", counting)
    ds = DjordjevicSarkar(dk=4.2, df=0.02)
    xs = CrossSection(
        conductors=(
            _xs.plane("g1", 0.0, 35e-6),
            _xs.strip("s1", "signal", 0.0, 0.2e-3, 0.2e-3, 35e-6),
        ),
        dielectrics=(XsDielectric(region=XsSlab(z_bottom=35e-6, z_top=0.4e-3), model=ds),),
        topology_hint="general",
        symmetric_about_u0=False,
    )
    sol = solve_bem(xs, list(np.logspace(6, 10, 30)))
    assert sol.n_solves == 6
    assert len(calls) == 1


def test_lu_solve_raises_on_exactly_singular_matrix() -> None:
    with pytest.raises(np.linalg.LinAlgError):
        bem_solver._lu_solve(np.zeros((4, 4), order="F"), np.ones((4, 1)))


def test_lu_solve_matches_numpy() -> None:
    rng = np.random.default_rng(3)
    a = rng.standard_normal((40, 40)) + 40 * np.eye(40)
    b = rng.standard_normal((40, 3))
    got = bem_solver._lu_solve(np.asfortranarray(a.copy()), b)
    assert np.allclose(got, np.linalg.solve(a, b), rtol=1e-12, atol=0)


def test_modal_batched_equals_per_frequency_reference() -> None:
    """The batched ``modal_decomposition`` equals the per-frequency evaluation of the same
    formulas (Zc = (ZY)^1/2 Y^-1, eigen decomposition of ZY)."""
    from oa_impedance.modal import modal_decomposition

    rng = np.random.default_rng(5)
    nf = 7
    freqs = np.array([0.0, 1e6, 1e7, 1e8, 1e9, 5e9, 1e10])
    c = np.array([[110.0, -20.0], [-20.0, 110.0]]) * 1e-12
    l_ = np.array([[330.0, 60.0], [60.0, 330.0]]) * 1e-9
    C = np.repeat(c[None], nf, axis=0) * (1 + 0.01 * rng.random((nf, 1, 1)))
    L = np.repeat(l_[None], nf, axis=0)
    w = 2 * np.pi * freqs
    R = (
        np.repeat(np.array([[2.0, 0.1], [0.1, 2.0]])[None], nf, axis=0)
        * np.sqrt(np.maximum(freqs, 1.0))[:, None, None]
        / 1e3
    )
    G = w[:, None, None] * C * 0.02
    res = modal_decomposition(freqs, R, L, G, C)
    c0 = 299792458.0
    for i, f in enumerate(freqs):
        if f <= 0.0:
            m = L[i] @ C[i]
            lam, t = np.linalg.eig(m)
            gam = np.sqrt(lam.astype(complex))
            zc = (t @ np.diag(gam) @ np.linalg.inv(t)) @ np.linalg.inv(C[i].astype(complex))
        else:
            z = R[i] + 2j * np.pi * f * L[i]
            y = G[i] + 2j * np.pi * f * C[i]
            lam, t = np.linalg.eig(z @ y)
            gam = np.sqrt(lam.astype(complex))
            gam = np.where(gam.real < 0, -gam, gam)
            zc = (t @ np.diag(gam) @ np.linalg.inv(t)) @ np.linalg.inv(y)
        zc = 0.5 * (zc + zc.T)
        assert np.allclose(res.zc_matrix[i], zc, rtol=1e-12, atol=0)
        dly = gam.real if f <= 0.0 else gam.imag / (2 * np.pi * f)
        assert np.allclose(np.sort(res.eps_eff[i]), np.sort((dly * c0) ** 2), rtol=1e-12)


def test_section_without_ground_plane(monkeypatch) -> None:
    """A section without any ground plane (the last signal is the reference) keeps working."""
    xs = CrossSection(
        conductors=(
            _xs.strip("s1", "signal", -0.3e-3, 0.2e-3, 0.2e-3, 35e-6),
            _xs.strip("s2", "signal", 0.3e-3, 0.2e-3, 0.2e-3, 35e-6),
        ),
        dielectrics=(
            XsDielectric(
                region=XsSlab(z_bottom=0.0, z_top=0.4e-3), model=_xs.slab(0, 1, 4.0).model
            ),
        ),
        topology_hint="general",
        symmetric_about_u0=False,
        background_dk=1.0,
    )
    _assert_close(*_both(xs, monkeypatch, [0.0, 1e9]))
