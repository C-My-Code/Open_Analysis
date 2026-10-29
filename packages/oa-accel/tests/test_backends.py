# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Backend behaviour, parametrized over the available backends (cpu always, cuda with the gpu
marker). CPU results are the oracle; the GPU is compared by the ADR-0009 section 5 classes."""

from __future__ import annotations

import numpy as np
import pytest
from accel_helpers import grid_laplacian, random_general, random_spd

from oa_accel.errors import AccelError
from oa_accel.policy import DevicePolicy, select
from oa_accel.tolerance import compare, relative_residual

BACKENDS = [
    "cpu",
    pytest.param("cuda", marks=pytest.mark.gpu),
]


@pytest.fixture(params=BACKENDS)
def backend(request: pytest.FixtureRequest):
    return select(DevicePolicy(device=request.param, fallback="error"))


@pytest.fixture
def oracle():
    return select("cpu")


def test_backend_name_and_report(backend) -> None:
    rep = backend.report()
    assert rep.backend == backend.name
    assert rep.fallbacks == []
    assert rep.cache_key().startswith(backend.name + "/")


@pytest.mark.parametrize("assume", ["gen", "sym", "pos"])
def test_dense_solve_matches_cpu(backend, oracle, assume) -> None:
    a = random_spd(200, seed=1) if assume != "gen" else random_general(200, seed=1)
    b = np.random.default_rng(2).standard_normal((200, 3))
    x = backend.dense_solve(a, b, assume)
    ref = oracle.dense_solve(a, b, assume)
    assert compare(x, ref, "dense").ok
    assert relative_residual(a, x, b) < 1e-10


def test_dense_solve_deterministic(backend) -> None:
    a = random_spd(150, seed=3)
    b = np.ones(150)
    x1 = backend.dense_solve(a, b, "pos")
    x2 = backend.dense_solve(a, b, "pos")
    assert x1.tobytes() == x2.tobytes()  # same device, same versions: bitwise identical


def test_dense_singular_is_structured_error(backend) -> None:
    a = np.zeros((20, 20))
    with pytest.raises(AccelError) as ei:
        backend.dense_solve(a, np.ones(20))
    assert ei.value.info.code == "ACCEL_SINGULAR"


@pytest.mark.parametrize("method", ["auto", "direct", "cg", "gmres"])
def test_sparse_solve_spd_matches_cpu(backend, oracle, method) -> None:
    a = grid_laplacian(30, 30, seed=4)
    b = np.random.default_rng(5).standard_normal((a.shape[0], 2))
    x = backend.sparse_solve(a, b, method, assume="spd", tol=1e-12)
    ref = oracle.sparse_solve(a, b, "direct", assume="spd")
    iterative = method in ("cg", "gmres")
    assert compare(x, ref, "sparse_iterative" if iterative else "sparse_direct").ok
    assert relative_residual(a, x, b) < 1e-9


def test_sparse_cg_jacobi(backend) -> None:
    a = grid_laplacian(25, 25, seed=6)
    b = np.ones(a.shape[0])
    x = backend.sparse_solve(a, b, "cg", "jacobi", 1e-12, assume="spd")
    assert relative_residual(a, x, b) < 1e-10


def test_sparse_not_converged_is_structured(backend) -> None:
    a = grid_laplacian(30, 30, seed=7)
    b = np.ones(a.shape[0])
    with pytest.raises(AccelError) as ei:
        backend.sparse_solve(a, b, "cg", None, 1e-14, assume="spd", maxiter=2)
    assert ei.value.info.code == "ACCEL_NOT_CONVERGED"


def test_sparse_matvec(backend) -> None:
    a = grid_laplacian(20, 20, seed=8)
    x = np.random.default_rng(9).standard_normal(a.shape[0])
    assert compare(backend.sparse_matvec(a, x), a @ x, "stencil").ok


def test_raster_stencil_matches_matrix(backend) -> None:
    import scipy.sparse as sp

    nx, ny = 12, 9
    wx, wy = 1.5, 0.7
    u = np.random.default_rng(10).standard_normal((nx, ny))
    t = lambda n: sp.diags([-1.0, 2.0, -1.0], [-1, 0, 1], shape=(n, n))  # noqa: E731
    a = wx * sp.kron(t(nx), sp.identity(ny)) + wy * sp.kron(sp.identity(nx), t(ny))
    y = backend.raster_stencil(u, (wx, wy))
    assert compare(y, (a @ u.ravel()).reshape(nx, ny), "stencil").ok


def test_raster_stencil_3d_is_spd_operator(backend) -> None:
    u = np.random.default_rng(11).standard_normal((6, 5, 4))
    v = np.random.default_rng(12).standard_normal((6, 5, 4))
    au = backend.raster_stencil(u, (1.0, 2.0, 3.0))
    av = backend.raster_stencil(v, (1.0, 2.0, 3.0))
    assert abs(np.vdot(v, au) - np.vdot(u, av)) < 1e-12  # symmetric
    assert np.vdot(u, au) > 0  # positive definite


def test_stencil_bad_input(backend) -> None:
    with pytest.raises(AccelError) as ei:
        backend.raster_stencil(np.ones(5), (1.0,))
    assert ei.value.info.code == "ACCEL_BAD_INPUT"
