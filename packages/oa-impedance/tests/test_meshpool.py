# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""WP-519: the meshing worker pool gives the mesh the in-process path gives, and ``solve_many``
returns the same results with and without it."""

from __future__ import annotations

import impedance_mix_helpers as mix
import numpy as np
import pytest

from oa_impedance.bem import meshpool
from oa_impedance.bem.geometry import MeshParams, build_problem
from oa_impedance.service import ImpedanceServiceImpl
from oa_results.container import as_np

FREQS = [1e6, 1e8, 3e9]


def _close(a: np.ndarray, b: np.ndarray) -> bool:
    """Equal up to the last digits: BLAS kernels sum in an order that depends on the alignment of
    the buffers, so two runs of the same solve differ by about 1e-13 of the largest entry."""
    return float(np.abs(a - b).max()) <= 1e-11 * max(float(np.abs(b).max()), 1e-300)


@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OA_IMPEDANCE_MESH_WORKERS", "2")
    monkeypatch.setenv("OA_IMPEDANCE_MESH_MIN", "2")
    yield
    meshpool.shutdown()


def test_pool_size_follows_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OA_IMPEDANCE_MESH_WORKERS", "0")
    assert meshpool.pool_size() == 0
    monkeypatch.setenv("OA_IMPEDANCE_MESH_WORKERS", "3")
    assert meshpool.pool_size() == 3
    monkeypatch.delenv("OA_IMPEDANCE_MESH_WORKERS")
    monkeypatch.setenv("OA_NUM_THREADS", "8")
    assert meshpool.pool_size() == 4  # half: the solve threads use the rest
    monkeypatch.setenv("OA_NUM_THREADS", "1")
    assert meshpool.pool_size() == 1


def test_small_batches_and_disabled_pool_stay_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    xs = mix.mix_sections(3)
    assert meshpool.mesh_many(xs, MeshParams()) is None  # below the default batch minimum
    monkeypatch.setenv("OA_IMPEDANCE_MESH_MIN", "2")
    monkeypatch.setenv("OA_IMPEDANCE_MESH_WORKERS", "0")
    assert meshpool.mesh_many(xs, MeshParams()) is None


def test_worker_mesh_equals_in_process_mesh(pool) -> None:
    sections = mix.mix_sections(4)
    futs = meshpool.mesh_many(sections, MeshParams())
    assert futs is not None
    for xs, fut in zip(sections, futs, strict=True):
        got = fut.result(timeout=120)
        ref = build_problem(xs, MeshParams())
        assert got is not None
        for name in ("a", "b", "cond", "kind", "zone1", "zone2", "face", "mid", "length"):
            assert np.array_equal(getattr(got.panels, name), getattr(ref.panels, name)), name
        assert got.cond_ids == ref.cond_ids and got.h_char == ref.h_char


def test_unmeshable_input_gives_none_not_an_exception(pool) -> None:
    futs = meshpool.mesh_many([mix.mix_section(1), None, mix.mix_section(2)], MeshParams())  # type: ignore[list-item]
    assert futs is not None
    got = [f.result(timeout=120) for f in futs]
    assert got[0] is not None and got[1] is None and got[2] is not None


def test_solve_many_with_the_pool_equals_without(pool, monkeypatch: pytest.MonkeyPatch) -> None:
    sections = mix.mix_sections(10, seed0=300)
    with_pool = ImpedanceServiceImpl(max_workers=3).solve_many(sections, FREQS, method="bem")
    monkeypatch.setenv("OA_IMPEDANCE_MESH_WORKERS", "0")
    without = ImpedanceServiceImpl(max_workers=3).solve_many(sections, FREQS, method="bem")
    for a, b in zip(with_pool, without, strict=True):
        for name in ("R", "L", "G", "C"):
            assert _close(as_np(getattr(a.rlgc, name)), as_np(getattr(b.rlgc, name))), name
        assert a.notes == b.notes


def test_results_do_not_depend_on_the_thread_count(pool) -> None:
    sections = mix.mix_sections(8, seed0=400)
    one = ImpedanceServiceImpl(max_workers=1).solve_many(sections, FREQS, method="bem")
    many = ImpedanceServiceImpl(max_workers=4).solve_many(sections, FREQS, method="bem")
    for a, b in zip(one, many, strict=True):
        assert _close(as_np(a.rlgc.C), as_np(b.rlgc.C))
        assert _close(as_np(a.params.zc_matrix), as_np(b.params.zc_matrix))


def test_pool_restarts_after_shutdown(pool) -> None:
    sections = mix.mix_sections(2)
    assert meshpool.mesh_many(sections, MeshParams()) is not None
    meshpool.shutdown()
    futs = meshpool.mesh_many(sections, MeshParams())
    assert futs is not None and all(f.result(timeout=120) is not None for f in futs)
