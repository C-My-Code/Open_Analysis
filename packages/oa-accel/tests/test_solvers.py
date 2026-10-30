# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""WP-504 solver entry points: batched dense, sparse SPD, matrix-free raster CG."""

from __future__ import annotations

import numpy as np
import pytest
from accel_helpers import grid_laplacian, random_general

from oa_accel.errors import AccelError
from oa_accel.policy import DevicePolicy, select
from oa_accel.solvers import THRESHOLDS, batched_dense_solve, cg_stencil, spd_solve
from oa_accel.tolerance import compare

BACKENDS = ["cpu", pytest.param("cuda", marks=pytest.mark.gpu)]


@pytest.fixture(params=BACKENDS)
def backend(request: pytest.FixtureRequest):
    return select(DevicePolicy(device=request.param, fallback="error"))


def test_thresholds_are_measured_defaults() -> None:
    assert THRESHOLDS.dense_n < 1500  # measured below the ADR-0009 placeholder
    import oa_accel.solvers as solvers

    with pytest.raises(AttributeError):
        _ = solvers.nothing


def test_batched_dense_matches_single_solves(backend) -> None:
    rng = np.random.default_rng(0)
    systems = []
    for n in (30, 30, 30, 80, 80, 12):
        a = random_general(n, seed=int(rng.integers(1000)))
        systems.append((a, rng.standard_normal((n, 2))))
    got = batched_dense_solve(backend, systems)
    for (a, b), x in zip(systems, got, strict=True):
        assert compare(x, np.linalg.solve(a, b), "dense").ok


def test_batched_dense_complex_and_vector_rhs(backend) -> None:
    rng = np.random.default_rng(1)
    systems = []
    for _ in range(4):
        a = rng.standard_normal((25, 25)) + 1j * rng.standard_normal((25, 25)) + 25 * np.eye(25)
        systems.append((a, rng.standard_normal(25) + 0j))
    got = batched_dense_solve(backend, systems)
    for (a, b), x in zip(systems, got, strict=True):
        assert compare(x, np.linalg.solve(a, b), "dense").ok


def test_batched_dense_singular_is_structured(backend) -> None:
    systems = [(np.zeros((10, 10)), np.ones(10))] * 2
    with pytest.raises(AccelError) as ei:
        batched_dense_solve(backend, systems)
    assert ei.value.info.code == "ACCEL_SINGULAR"


@pytest.mark.parametrize("method", ["auto", "cg"])
def test_spd_solve_matches_cpu(backend, method) -> None:
    a = grid_laplacian(40, 40, seed=3)
    b = np.random.default_rng(4).standard_normal(a.shape[0])
    x, label = spd_solve(backend, a, b, method=method, tol=1e-12)
    ref, ref_label = spd_solve(select("cpu"), a, b, tol=1e-12)
    assert ref_label == "cpu-direct"
    assert compare(x, ref, "sparse_iterative" if method == "cg" else "sparse_direct").ok
    assert label.startswith("cpu") == (backend.name == "cpu")


def test_cg_stencil_matches_assembled_laplacian(backend) -> None:
    import scipy.sparse as sp
    import scipy.sparse.linalg as spla

    nx, ny = 24, 17
    wx, wy = 2.0, 3.0
    ix = sp.diags([-1.0, 2.0, -1.0], [-1, 0, 1], shape=(nx, nx))
    iy = sp.diags([-1.0, 2.0, -1.0], [-1, 0, 1], shape=(ny, ny))
    # u[y, x]: axis 0 is y, axis 1 is x; weights follow the array axes
    lap = wy * sp.kron(iy, sp.identity(nx)) + wx * sp.kron(sp.identity(ny), ix)
    rhs = np.random.default_rng(5).standard_normal((ny, nx))
    ref = spla.spsolve(lap.tocsc(), rhs.ravel()).reshape(ny, nx)
    u, its = cg_stencil(backend, (wy, wx), rhs, tol=1e-12)
    assert its > 0
    assert compare(u, ref, "sparse_iterative").ok


def test_cg_stencil_not_converged_is_structured(backend) -> None:
    rhs = np.random.default_rng(6).standard_normal((20, 20))
    with pytest.raises(AccelError) as ei:
        cg_stencil(backend, (1.0, 1.0), rhs, tol=1e-14, maxiter=2)
    assert ei.value.info.code == "ACCEL_NOT_CONVERGED"
