# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared problem builders for the oa_fdtd tests."""

from __future__ import annotations

import numpy as np

from oa_fdtd.analysis import matrix_pencil
from oa_fdtd.grid import C0, Grid
from oa_fdtd.model import Model, Probe, SoftSource, Waveform, compile_model
from oa_fdtd.reference import NumpyEngine


def graded_lines(length: float, n: int, ratio: float) -> np.ndarray:
    """``n`` cells over ``length`` with geometric growth ``ratio`` per cell (1 = uniform)."""
    if ratio == 1.0:
        return np.linspace(0.0, length, n + 1)
    w = ratio ** np.arange(n)
    return np.concatenate([[0.0], np.cumsum(w) / w.sum() * length])


def refine(lines: np.ndarray) -> np.ndarray:
    """Bisect every cell (keeps the grading ratios)."""
    mid = 0.5 * (lines[1:] + lines[:-1])
    out = np.empty(lines.size + mid.size)
    out[0::2] = lines
    out[1::2] = mid
    return out


def cavity_mode_freq(a: float, b: float, d: float, m: int, n: int, p: int) -> float:
    return 0.5 * C0 * float(np.sqrt((m / a) ** 2 + (n / b) ** 2 + (p / d) ** 2))


def cavity_frequencies(
    lines: tuple[np.ndarray, np.ndarray, np.ndarray],
    targets: list[float],
    dtype: str = "float64",
    engine: str = "numpy",
    t_end: float = 60e-9,
) -> list[float]:
    """Excite a PEC box and return the matrix-pencil frequency nearest to each target [Hz]."""
    g = Grid(*lines)
    ext = [float(v[-1] - v[0]) for v in lines]
    wf = Waveform("gauss_mod", f0=0.5 * (min(targets) + max(targets)), bandwidth=2.0 * max(targets))
    model = Model(g, wf, np.array([1e9]))
    pos = (0.31, 0.37, 0.43)
    p = tuple(e * q for e, q in zip(ext, pos, strict=True))
    q = tuple(e * r for e, r in zip(ext, (0.67, 0.29, 0.61), strict=True))
    for ax in range(3):
        model.sources.append(SoftSource(f"s{ax}", ax, p, p))
    for ax in range(3):
        model.probes.append(Probe(f"p{ax}", "E", ax, q, q))
    comp = compile_model(model, dtype)
    if engine == "numpy":
        eng = NumpyEngine(comp, dtype, record_series=True)
    else:
        from oa_fdtd.engine import CupyEngine

        eng = CupyEngine(comp, dtype, record_series=True)
    nsteps = int(t_end / comp.dt)
    eng.step(nsteps)
    ser = eng.series()
    found: list[float] = []
    sig = sum(ser[f"p{ax}"][0] for ax in range(3))
    f, _ = matrix_pencil(sig, comp.dt, order=2 * len(targets) + 10, decimate=4)
    for t in targets:
        found.append(float(f[np.argmin(np.abs(f - t))]))
    return found


def line_model(
    ny: int, d: float, freqs: np.ndarray, src_y: int, probe_y: int, cells: int = 10, dc: bool = True
) -> Model:
    """Uniform TEM line (cubic cells, PMC x, PEC z, CPML along y); E_z plane source and probe."""
    from oa_fdtd.pml import CpmlSpec

    lines = np.arange(3) * d
    g = Grid(lines, np.arange(ny + 1) * d, lines)
    m = Model(
        g,
        Waveform("gauss", bandwidth=45e9) if dc else Waveform("gauss_mod", 20e9, 40e9),
        freqs,
        walls=("pmc", "pmc", "cpml", "cpml", "pec", "pec"),
        cpml=CpmlSpec(cells=cells),
    )
    ext = (0.0, 2 * d)
    m.sources.append(SoftSource("s", 2, (ext[0], src_y * d, 0.0), (ext[1], src_y * d, 2 * d)))
    m.probes.append(
        Probe("p", "E", 2, (ext[0], probe_y * d, 0.0), (ext[1], probe_y * d, 2 * d), "mean")
    )
    return m


def cpml_reflection(
    freqs: np.ndarray, dtype: str, engine: str, cells: int = 10, steps: int = 3000, side: str = "hi"
) -> np.ndarray:
    """|reflected / incident| at ``freqs`` for normal incidence on one CPML of ``cells`` cells."""
    from oa_fdtd.run import run

    d = 299792458.0 / 40e9 / 20.0
    n = 2 * cells + 60
    extra = 1400
    s0, p0 = cells + 10, cells + 30  # source and probe cells from the near end
    if side == "hi":
        short = line_model(n, d, freqs, s0, p0, cells)
        long_ = line_model(n + extra, d, freqs, s0, p0, cells)
    else:  # mirror: the wave runs towards the low-y CPML
        short = line_model(n, d, freqs, n - s0, n - p0, cells)
        long_ = line_model(n + extra, d, freqs, n - s0 + extra, n - p0 + extra, cells)
    a = run(short, engine=engine, dtype=dtype, end_db=None, max_steps=steps).probes["p"][0]
    b = run(long_, engine=engine, dtype=dtype, end_db=None, max_steps=steps).probes["p"][0]
    return np.abs(a - b) / np.abs(b)
