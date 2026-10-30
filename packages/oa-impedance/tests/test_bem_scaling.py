# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""WP-512: window truncation, graded far-field panels, memory budget and the GMRES path."""

from __future__ import annotations

import _xs
import numpy as np
import pytest

from oa_impedance.bem import MeshParams, solve_bem, solver
from oa_impedance.bem.geometry import build_problem
from oa_impedance.bem.solver import dense_peak_bytes
from oa_impedance.errors import SectionTooLarge
from oa_impedance.service import ImpedanceServiceImpl
from oa_model.xsection import CrossSection, XsConductor, XsPlane, XsTrapezoid
from oa_testkit import xsections as tk


@pytest.fixture(autouse=True)
def _host_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests check the host section budget (coarsening, GMRES, refusal); the GPU path has
    its own (device) budget and is covered by test_bem_gpu.py."""
    monkeypatch.setenv("OA_DEVICE", "cpu")


# the pre-WP-512 meshing: open boundary at 20 x the whole window, no far-field coarsening,
# every plane face capped at 3 x its thickness
LEGACY = MeshParams(window=False, far_ratio=0.0, plane_relax=0.0)


def _z(xs: CrossSection, mesh: MeshParams) -> float:
    r = ImpedanceServiceImpl(mesh=mesh).solve(xs, [0.0], method="bem")
    arr = r.params.z0 if r.params.z0 is not None else r.params.z_diff
    assert arr is not None
    return float(np.abs(arr.array[0]))


def _pcb_microstrip(window: float | None = None, grounds: bool = True) -> CrossSection:
    """35 um trace on 100 um FR-4 over a plane, a 10 um mask above it, coplanar grounds."""
    um = 1e-6
    cond = [
        XsConductor(
            id="g_plane", role="ground", shape=XsPlane(z_bottom=-15 * um, thickness=15 * um)
        ),
        XsConductor(
            id="s1",
            role="signal",
            shape=XsTrapezoid(
                u_center=0.0,
                z_bottom=100 * um,
                w_bottom=150 * um,
                w_top=150 * um,
                thickness=35 * um,
            ),
        ),
    ]
    if grounds:
        for k, u in enumerate((-600 * um, 600 * um)):
            cond.append(
                XsConductor(
                    id=f"g{k + 1}",
                    role="ground",
                    shape=XsTrapezoid(
                        u_center=u,
                        z_bottom=100 * um,
                        w_bottom=400 * um,
                        w_top=400 * um,
                        thickness=35 * um,
                    ),
                )
            )
    del window
    return CrossSection(
        conductors=tuple(cond),
        dielectrics=(
            _xs.slab(0.0, 100 * um, 4.4),
            _xs.slab(100 * um, 135 * um, 3.3),
            _xs.slab(135 * um, 145 * um, 3.3),
        ),
        topology_hint="microstrip",
    )


def test_window_and_grading_keep_testkit_z0_within_0005_percent() -> None:
    worst = 0.0
    for case in tk.all_cases():
        a, b = _z(case.xs, MeshParams()), _z(case.xs, LEGACY)
        worst = max(worst, abs(a / b - 1))
    assert worst < 5e-4


def test_pcb_section_is_independent_of_open_boundary_length() -> None:
    """Thin layers on a long section: the probe offsets that classify the zones beside a long
    interface must stay inside the thinnest layer (was a 1.6 % error beyond ~10 mm)."""
    xs = _pcb_microstrip()
    short = _z(xs, MeshParams(truncation=5.0))
    long = _z(xs, MeshParams(truncation=80.0))
    assert abs(short / long - 1) < 1e-3


def test_far_copper_does_not_dominate_the_panel_count() -> None:
    xs = _pcb_microstrip()
    n_new = build_problem(xs, MeshParams()).panels.n
    n_old = build_problem(xs, LEGACY).panels.n
    assert n_new < 0.7 * n_old


def test_budget_refusal_is_a_structured_error() -> None:
    xs = _pcb_microstrip()
    with pytest.raises(SectionTooLarge) as ei:
        solve_bem(xs, [0.0], budget=1 << 20)
    info = ei.value.info()
    assert info.code == "IMPEDANCE.SECTION_TOO_LARGE"
    assert info.hint


def test_dense_estimate_scales_with_n_squared() -> None:
    assert dense_peak_bytes(2000, 800) > 3.5 * dense_peak_bytes(1000, 400) - (32 << 20)
    assert dense_peak_bytes(1000, 400, complex_=True) > dense_peak_bytes(1000, 400)


def test_gmres_matches_dense(monkeypatch: pytest.MonkeyPatch) -> None:
    xs = _xs.stripline(0.6, er=4.0, t_over_b=0.02)
    dense = solve_bem(xs, [0.0])
    assert dense.mode == "dense"
    # pretend the dense system does not fit and refuse to coarsen: the matrix-free path runs
    monkeypatch.setattr(solver, "dense_peak_bytes", lambda n, nc, complex_=False: 10**12)
    monkeypatch.setattr(solver, "_MAX_COARSEN", 0)
    it = solve_bem(xs, [0.0])
    assert it.mode == "gmres"
    assert np.allclose(it.C, dense.C, rtol=1e-6)
    assert np.allclose(it.C0, dense.C0, rtol=1e-9)


def test_over_budget_section_is_coarsened_not_refused() -> None:
    xs = _pcb_microstrip()
    full = solve_bem(xs, [0.0])
    small = solve_bem(xs, [0.0], budget=int(full.peak_bytes * 0.8))
    assert small.n_panels < full.n_panels
    assert any("coarsened" in n for n in small.notes)
    assert np.allclose(small.C, full.C, rtol=2e-2)
