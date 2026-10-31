# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""WP-504: the GPU dense path (device assembly + cuSOLVER) against the CPU path (ADR-0009 s5)."""

from __future__ import annotations

import os

import _xs
import impedance_mix_helpers as mix
import numpy as np
import pytest

from oa_impedance.bem import solve_bem
from oa_impedance.bem import solver as bem_solver
from oa_testkit import xsections as tk

_gpu = pytest.mark.skipif(os.environ.get("OA_RUN_GPU") != "1", reason="GPU tests need OA_RUN_GPU=1")
FREQS = [0.0, 1e8, 3e9]


def _solve(xs, device: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OA_DEVICE", device)
    return solve_bem(xs, FREQS)


def test_cpu_device_never_touches_the_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OA_DEVICE", "cpu")
    geo = bem_solver.prepare(_xs.stripline(0.5, 1.0, 0.1))
    assert geo.be is None and isinstance(geo.P, np.ndarray)


@_gpu
@pytest.mark.parametrize("case", tk.all_cases(), ids=lambda c: c.name)
def test_gpu_matches_cpu_on_testkit(case, monkeypatch: pytest.MonkeyPatch) -> None:
    cpu = _solve(case.xs, "cpu", monkeypatch)
    gpu = _solve(case.xs, "cuda", monkeypatch)
    assert gpu.n_panels == cpu.n_panels
    for name in ("C", "C0", "L_ext", "R_ac", "R_dc"):
        a, b = getattr(gpu, name), getattr(cpu, name)
        scale = max(float(np.abs(b).max()), 1e-300)
        assert float(np.abs(a - b).max()) / scale < 1e-10, name


@_gpu
def test_gpu_path_is_used_and_released(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OA_DEVICE", "cuda")
    xs = _xs.stripline(0.5, 1.0, 0.1)
    geo = bem_solver.prepare(xs)
    assert geo.be is not None and geo.reserved_dev > 0
    sol = solve_bem(xs, [0.0], geo=geo)
    assert geo.reserved_dev == 0 and geo.P is None
    assert any("dense LU on cuda" in n for n in sol.notes)
    gate = bem_solver._dev_gate(geo.be)
    assert gate.used == 0


@_gpu
def test_gpu_fault_falls_back_to_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OA_DEVICE", "cuda")
    xs = _xs.stripline(0.5, 1.0, 0.1)
    ref = _solve(xs, "cpu", monkeypatch)
    monkeypatch.setenv("OA_DEVICE", "cuda")
    geo = bem_solver.prepare(xs)
    be = geo.be
    assert be is not None
    be.inject_fault(RuntimeError("boom"))  # type: ignore[attr-defined]
    sol = solve_bem(xs, FREQS, geo=geo)
    assert np.allclose(sol.C, ref.C, rtol=1e-10, atol=0)
    assert be.report().fallbacks  # recorded
    assert geo.be is None and any("host dense path used" in n for n in sol.notes)
    assert bem_solver._dev_gate(be).used == 0
    be.inject_fault(None)  # type: ignore[attr-defined]


# ---- WP-519: sections like those of the --mix board, device against host. The bound is 1e-9 of
# the largest entry: two exact float64 solvers of these systems differ by their conditioning
# noise, a few 1e-10 (the whole-system and the block elimination on the host differ by as much).

MIX_TOL = 1e-9


def _assert_same(a, b, tol: float = MIX_TOL) -> None:
    for name in ("C", "C0", "L_ext", "R_ac", "R_dc"):
        x, y = getattr(a, name), getattr(b, name)
        scale = max(float(np.abs(y).max()), 1e-300)
        assert float(np.abs(x - y).max()) / scale < tol, name


@_gpu
@pytest.mark.parametrize("seed", range(100, 108))
def test_gpu_matches_cpu_on_mix_sections(seed: int, monkeypatch: pytest.MonkeyPatch) -> None:
    xs = mix.mix_section(seed)
    cpu = _solve(xs, "cpu", monkeypatch)
    gpu = _solve(xs, "cuda", monkeypatch)
    assert gpu.n_panels == cpu.n_panels and gpu.n_solves == cpu.n_solves
    assert any("dense LU on cuda" in n for n in gpu.notes)
    _assert_same(gpu, cpu)


@_gpu
def test_parallel_batch_assembles_on_the_device_and_factors_on_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xs = mix.mix_section(101)
    cpu = _solve(xs, "cpu", monkeypatch)
    monkeypatch.setenv("OA_DEVICE", "cuda")
    monkeypatch.setattr(bem_solver, "_PAR_BATCH_N", 10_000)
    with bem_solver.batch(parallel=True):
        geo = bem_solver.prepare(xs)
        assert geo.be is None and isinstance(geo.P, np.ndarray) and geo.reserved_dev == 0
        sol = solve_bem(xs, FREQS, geo=geo)
    assert any("influence matrices on cuda" in n for n in sol.notes)
    _assert_same(sol, cpu)


@_gpu
def test_gpu_batch_through_the_service_matches_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    from oa_impedance.service import ImpedanceServiceImpl
    from oa_results.container import as_np

    sections = mix.mix_sections(12, seed0=500)
    out = {}
    for dev in ("cpu", "cuda"):
        monkeypatch.setenv("OA_DEVICE", dev)
        monkeypatch.setattr(bem_solver, "_PAR_BATCH_N", 900)  # some on the device, some assembled
        out[dev] = ImpedanceServiceImpl(max_workers=3).solve_many(sections, FREQS, method="bem")
    for c, g in zip(out["cpu"], out["cuda"], strict=True):
        for name in ("R", "L", "G", "C"):
            x, y = as_np(getattr(g.rlgc, name)), as_np(getattr(c.rlgc, name))
            assert float(np.abs(x - y).max()) / max(float(np.abs(y).max()), 1e-300) < MIX_TOL
