# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""WP-504: the GPU sparse SPD path of ``oa_pi.dc.solvers`` against the CPU solvers."""

from __future__ import annotations

import os

import numpy as np
import pytest
import scipy.sparse as sp

from oa_pi.dc.solvers import solve_spd

_gpu = pytest.mark.skipif(os.environ.get("OA_RUN_GPU") != "1", reason="needs OA_RUN_GPU=1")


def _laplacian(n: int) -> sp.csr_matrix:
    d = sp.diags([-1.0, 2.0, -1.0], [-1, 0, 1], shape=(n, n))
    eye = sp.identity(n)
    return (sp.kron(d, eye) + sp.kron(eye, d) + 1e-3 * sp.identity(n * n)).tocsr()


def test_cpu_device_uses_a_cpu_solver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OA_DEVICE", "cpu")
    a = _laplacian(20)
    _, used = solve_spd(a, np.ones(a.shape[0]))
    assert used in ("superlu", "pardiso", "cholmod", "amg")


def test_explicit_solver_stays_on_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OA_DEVICE", "cuda")
    a = _laplacian(20)
    _, used = solve_spd(a, np.ones(a.shape[0]), "superlu")
    assert used == "superlu"


@_gpu
def test_gpu_matches_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    a = _laplacian(60)
    b = np.random.default_rng(0).standard_normal(a.shape[0])
    monkeypatch.setenv("OA_DEVICE", "cpu")
    ref, _ = solve_spd(a, b)
    monkeypatch.setenv("OA_DEVICE", "cuda")
    x, used = solve_spd(a, b)
    assert used in ("cudss", "cg-jacobi-gpu")
    assert float(np.abs(x - ref).max()) < 1e-9
