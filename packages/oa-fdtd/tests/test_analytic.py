# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""ADR-0010 section 9 cases 1, 2, 3, 7 and the Yee dispersion check, on both engines."""

from __future__ import annotations

import numpy as np
import pytest
from fdtd_helpers import (
    cavity_frequencies,
    cavity_mode_freq,
    cpml_reflection,
    graded_lines,
    line_model,
    refine,
)

from oa_fdtd.grid import C0, ETA0, Grid
from oa_fdtd.model import LumpedPort, Model, SoftSource, Waveform, compile_model
from oa_fdtd.reference import NumpyEngine
from oa_fdtd.run import run, sparams

A, B, D = 0.030, 0.025, 0.035
MODES = [(1, 0, 1), (0, 1, 1), (1, 1, 0)]  # TE101, TE011, TM110
TARGETS = [cavity_mode_freq(A, B, D, *m) for m in MODES]


def _richardson(ratio: float, engine: str, dtype: str) -> tuple[list[float], float]:
    """Mode errors after Richardson extrapolation over 3 grids, and the observed order."""
    base = (
        graded_lines(A, 10, ratio),
        graded_lines(B, 8, ratio),
        graded_lines(D, 12, ratio),
    )
    levels = [base]
    for _ in range(2):
        levels.append(tuple(refine(v) for v in levels[-1]))  # type: ignore[arg-type]
    res = [cavity_frequencies(lv, TARGETS, dtype, engine, t_end=30e-9) for lv in levels]
    f1, f2, f3 = (np.array(r) for r in res)
    order = np.log2(np.abs(f1 - f2) / np.abs(f2 - f3))
    extrap = f3 + (f3 - f2) / 3.0  # second-order Richardson
    err = list(np.abs(extrap - np.array(TARGETS)) / np.array(TARGETS))
    return err, float(np.median(order))


@pytest.mark.parametrize(
    "ratio",
    [1.0, pytest.param(1.05, marks=pytest.mark.slow)],
    ids=["uniform", "graded"],
)
def test_cavity_richardson_numpy(ratio: float) -> None:
    err, order = _richardson(ratio, "numpy", "float64")
    assert max(err) < 5e-4  # 0.05 %
    assert 1.8 < order < 2.2


@pytest.mark.gpu
@pytest.mark.parametrize("ratio", [1.0, 1.05], ids=["uniform", "graded"])
def test_cavity_richardson_cuda_float32(ratio: float) -> None:
    err, order = _richardson(ratio, "cuda", "float32")
    assert max(err) < 5e-4
    assert 1.8 < order < 2.2


@pytest.mark.parametrize("engine", ["numpy", pytest.param("cuda", marks=pytest.mark.gpu)])
@pytest.mark.parametrize("side", ["hi", "lo"])
def test_cpml_reflection_below_60_db(engine: str, side: str) -> None:
    f = np.array([0.1e9, 0.5e9, 1e9, 5e9, 10e9, 20e9, 30e9, 40e9])
    dtype = "float64" if engine == "numpy" else "float32"
    r = cpml_reflection(f, dtype, engine, side=side)
    assert float(20 * np.log10(r.max())) < -60.0


@pytest.mark.parametrize("engine", ["numpy", pytest.param("cuda", marks=pytest.mark.gpu)])
def test_cpml_late_time_energy_decay(engine: str) -> None:
    d = C0 / 40e9 / 20.0
    m = line_model(80, d, np.array([1e9]), 20, 40, dc=False)
    res = run(m, engine=engine, dtype="float64", end_db=-80.0, max_steps=20000, min_steps=200)
    assert res.stopped_by == "end_criterion"
    assert res.energy_db <= -80.0


def _tem_model(f_max: float = 3e9) -> Model:
    w, h = 0.008, 0.004
    z0 = ETA0 * h / w
    nx, nz, ny, dy = 4, 4, 120, 0.5e-3
    g = Grid(np.linspace(0, w, nx + 1), np.arange(ny + 1) * dy, np.linspace(0, h, nz + 1))
    ports = [
        LumpedPort("p1", 2, (0, 0.0, 0), (w, 0.0, h), z0),
        LumpedPort("p2", 2, (0, ny * dy, 0), (w, ny * dy, h), z0),
    ]
    return Model(
        g,
        Waveform("gauss_mod", f_max / 2.0, f_max),
        np.linspace(0.5e9, f_max, 12),
        walls=("pmc", "pmc", "pmc", "pmc", "pec", "pec"),
        ports=ports,
    )


@pytest.mark.parametrize("engine", ["numpy", pytest.param("cuda", marks=pytest.mark.gpu)])
def test_parallel_plate_tem_line(engine: str) -> None:
    """Z0 = eta h / w: the port R = Z0 gives a matched line; |S21| = 1 to 1e-4 below 3 GHz."""
    m = _tem_model(20e9)
    dtype = "float64" if engine == "numpy" else "float32"
    res = run(m, engine=engine, dtype=dtype, end_db=-70.0, max_steps=20000)
    s = sparams(m, res)
    assert np.abs(s[0, 0]).max() < 10 ** (-45 / 20)  # S11 below -45 dB up to 20 GHz
    # was 3.8e-3 at 20 GHz before the semi-implicit port voltage was averaged (port_voltage_factor)
    assert np.abs(np.abs(s[1, 0]) - 1.0).max() < 1e-4
    assert np.abs(s[1, 0] - s[0, 1]).max() < 1e-6  # reciprocity


def test_mismatched_port_reflection() -> None:
    """A port R = 2 Z0 gives |S11| = 1/3 on the reference engine (Z0 is exact to 0.1 %)."""
    m = _tem_model()
    z0 = m.ports[0].r_ohm
    m.ports = [
        LumpedPort("p1", 2, m.ports[0].lo, m.ports[0].hi, 2 * z0),
        m.ports[1],
    ]
    res = run(m, engine="numpy", dtype="float64", end_db=-70.0, max_steps=20000)
    s = sparams(m, res)
    assert np.abs(np.abs(s[0, 0]) - 1.0 / 3.0).max() < 1e-3


@pytest.mark.parametrize("engine", ["numpy", pytest.param("cuda", marks=pytest.mark.gpu)])
def test_yee_dispersion(engine: str) -> None:
    """Numerical phase constant vs the exact Yee dispersion relation along a cubic-cell line."""
    d = C0 / 40e9 / 20.0
    freqs = np.array([2e9, 5e9, 10e9, 20e9, 30e9])
    m = line_model(120, d, freqs, 60, 70)
    m.probes.clear()
    from oa_fdtd.model import Probe

    for name, yy in (("a", 75), ("b", 95)):
        m.probes.append(Probe(name, "E", 2, (0, yy * d, 0), (2 * d, yy * d, 2 * d), "mean"))
    dtype = "float64" if engine == "numpy" else "float32"
    res = run(m, engine=engine, dtype=dtype, end_db=None, max_steps=1200)
    ratio = res.probes["b"][0] / res.probes["a"][0]
    k_num = -np.unwrap(np.angle(ratio)) / (20 * d)
    dt = res.dt
    k_yee = 2.0 / d * np.arcsin(d / (C0 * dt) * np.sin(np.pi * freqs * dt))
    tol = 1e-3 if dtype == "float64" else 2e-3
    assert np.abs(k_num / k_yee - 1.0).max() < tol
    assert np.abs(k_num / (2 * np.pi * freqs / C0) - 1.0).max() > 1e-4  # dispersion is visible


def _exact_energy(
    eng: NumpyEngine, h_prev: list[np.ndarray], fields: dict[str, np.ndarray]
) -> float:
    c = eng.c
    total = 0.0
    from oa_fdtd.grid import EPS0, MU0

    for ci, (en, hn) in enumerate((("Ex", "Hx"), ("Ey", "Hy"), ("Ez", "Hz"))):
        w = eng._weights("E", ci)
        total += 0.5 * float(np.sum((c.eps_tab[c.idx[ci]] * EPS0)[None] * w * fields[en] ** 2))
        wh = eng._weights("H", ci)
        total += 0.5 * MU0 * float(np.sum(wh * h_prev[ci] * fields[hn]))
    return total


def _drift(engine: str, dtype: str, steps: int) -> float:
    lines = (graded_lines(0.02, 8, 1.1), graded_lines(0.017, 6, 1.0), graded_lines(0.024, 9, 0.9))
    g = Grid(*lines)
    m = Model(g, Waveform("gauss_mod", 8e9, 16e9), np.array([1e9]))
    m.sources.append(SoftSource("s", 2, (0.007, 0.006, 0.01), (0.007, 0.006, 0.01)))
    c = compile_model(m, dtype)
    ref = NumpyEngine(c, "float64")  # holds weights and tables
    if engine == "numpy":
        eng: NumpyEngine | object = NumpyEngine(c, dtype)
    else:
        from oa_fdtd.engine import CupyEngine

        eng = CupyEngine(c, dtype, max_steps=steps + 1000, use_graph=False)

    def snap() -> tuple[list[np.ndarray], dict[str, np.ndarray]]:
        f = (
            eng.get_fields()
            if engine == "cuda"
            else {  # type: ignore[attr-defined]
                n: a.astype(np.float64)
                for n, a in zip(("Ex", "Ey", "Ez", "Hx", "Hy", "Hz"), (*eng.E, *eng.H), strict=True)  # type: ignore[attr-defined]
            }
        )
        return [f["Hx"], f["Hy"], f["Hz"]], f

    eng.step(800)  # type: ignore[attr-defined]  # the source pulse has ended
    energies = []
    for n_more in (0, steps - 801):
        if n_more:
            eng.step(n_more)  # type: ignore[attr-defined]
        h_prev, f0 = snap()
        eng.step(1)  # type: ignore[attr-defined]
        _, f1 = snap()
        # E^n with H^{n-1/2} . H^{n+1/2}: the conserved leapfrog energy
        energies.append(
            _exact_energy(ref, h_prev, {**f0, "Hx": f1["Hx"], "Hy": f1["Hy"], "Hz": f1["Hz"]})
        )
    return abs(energies[1] - energies[0]) / energies[0]


def test_closed_box_energy_drift_numpy() -> None:
    assert _drift("numpy", "float64", 100_000) < 1e-6


@pytest.mark.gpu
def test_closed_box_energy_drift_cuda_float64_1e5_steps() -> None:
    assert _drift("cuda", "float64", 100_000) < 1e-6


@pytest.mark.gpu
def test_closed_box_energy_drift_cuda_float32_reported() -> None:
    drift = _drift("cuda", "float32", 100_000)
    print(f"float32 energy drift over 1e5 steps: {drift:.3e}")
    assert drift < 1e-2
