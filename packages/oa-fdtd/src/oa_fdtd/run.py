# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run driver: engine choice, memory check, energy end criterion, source spectra, S-parameters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from oa_fdtd.model import Compiled, Model, ModelError, compile_model

FloatArray = npt.NDArray[np.float64]
ComplexArray = npt.NDArray[np.complex128]


@dataclass
class RunResult:
    probes: dict[str, ComplexArray]  # name -> (B, F) DFT [field units x s]
    source_dft: ComplexArray  # (channels, F) DFT of the source waveforms [s]
    steps: int
    dt: float  # [s]
    engine: str
    dtype: str
    stopped_by: str  # "end_criterion" | "max_steps"
    energy_db: float  # final field energy relative to its peak [dB]
    peak_bytes: int  # GPU pool high-water mark [bytes], 0 on CPU
    report: dict[str, Any] = field(default_factory=dict)  # oa_accel AccelReport
    series: dict[str, FloatArray] = field(default_factory=dict)
    port_r: FloatArray = field(default_factory=lambda: np.zeros(0))  # reference impedances [ohm]


def source_spectrum(c: Compiled, steps: int) -> ComplexArray:
    """``sum_n s_c(t_{n+1/2}) exp(-j w t_{n+1/2}) dt`` for every channel."""
    t = (np.arange(steps) + 0.5) * c.dt
    ph = np.exp(-1j * 2.0 * np.pi * c.frequencies[:, None] * t[None, :]) * c.dt  # (F, steps)
    out = np.zeros((len(c.channels), c.frequencies.size), np.complex128)
    for ci, ch in enumerate(c.channels):
        out[ci] = ph @ ch["waveform"](t)
    return out


def make_engine(
    c: Compiled, engine: str, dtype: str, record_series: bool, max_steps: int
) -> tuple[Any, dict[str, Any]]:
    """Build the engine; ``engine`` is ``numpy``, ``cuda`` or ``auto`` (oa_accel policy)."""
    report: dict[str, Any] = {"backend": "cpu"}
    use_cuda = engine == "cuda"
    xp = None
    if engine in ("auto", "cuda"):
        from oa_accel import DevicePolicy, Problem, select

        pol = DevicePolicy(
            device="cuda" if engine == "cuda" else None,
            problem=Problem(kind="fdtd", cells=c.grid.cells),
            fallback="error" if engine == "cuda" else "cpu",
        )
        be = select(pol)
        report = be.report().to_dict()
        use_cuda = be.name != "cpu"
        if use_cuda:
            from oa_fdtd.engine import estimate_bytes

            be.memory.require(estimate_bytes(c, np.dtype(dtype).itemsize), "fdtd")
            xp = be.xp
    if use_cuda:
        from oa_fdtd.engine import CupyEngine

        return CupyEngine(c, dtype, record_series, xp=xp, max_steps=max_steps), report
    from oa_fdtd.reference import NumpyEngine

    return NumpyEngine(c, dtype, record_series), report


def run(
    model: Model,
    *,
    engine: str = "auto",
    dtype: str = "float32",
    max_steps: int = 100_000,
    end_db: float | None = -50.0,
    min_steps: int = 0,
    check_every: int = 64,
    record_series: bool = False,
) -> RunResult:
    """Run until the field energy is ``end_db`` below its peak (checked every ``check_every``
    steps once ``min_steps`` have run) or ``max_steps``. ``end_db=None`` runs ``max_steps``.
    """
    if engine == "numpy" and dtype == "float32":
        dtype = "float64"  # the reference engine is float64 unless asked otherwise explicitly
    c = compile_model(model, dtype)
    eng, report = make_engine(c, engine, dtype, record_series, max_steps)
    peak = 0.0
    e_db = 0.0
    stopped = "max_steps"
    done = 0
    while done < max_steps:
        n = min(check_every, max_steps - done)
        eng.step(n)
        done += n
        if end_db is None:
            continue
        e = eng.energy()
        peak = max(peak, e)
        e_db = 10.0 * np.log10(e / peak) if peak > 0 and e > 0 else 0.0
        if not np.isfinite(e):
            raise ModelError("FDTD_UNSTABLE", "field energy is not finite")
        if done >= min_steps and peak > 0 and e_db <= end_db:
            stopped = "end_criterion"
            break
    probes = eng.results()
    sums = {f"V:{q['name']}": q["probe_parts"] for q in c.ports if q.get("probe_parts")}
    sums.update(c.probe_sums)
    for name, parts in sums.items():  # mode ports record one probe per field component: sum them
        probes[name] = sum(probes.pop(q) for q in parts)
    return RunResult(
        probes=probes,
        source_dft=source_spectrum(c, done),
        steps=done,
        dt=c.dt,
        engine=eng.name,
        dtype=dtype,
        stopped_by=stopped,
        energy_db=e_db,
        peak_bytes=eng.peak_bytes(),
        report=report,
        series=eng.series() if record_series else {},
        port_r=np.array([float(q["r_ohm"]) for q in c.ports]),
    )


def port_voltage_factor(dt: float, frequencies: FloatArray) -> ComplexArray:
    """Map the recorded ``E^{n+1}`` DFT (stamped ``t_{n+1}``) to the voltage the port resistor sees.

    The lumped-port loss is semi-implicit: the resistor carries the time average
    ``(E^n + E^{n+1}) / 2`` at ``t_{n+1/2}``, the stamp of the source. In the frequency domain that
    is the real factor ``cos(w dt / 2)`` (the DFT carries absolute time stamps, so no phase).
    Without it the power waves are normalised with a voltage that is too large by
    ``1 / cos(w dt / 2)``: an O(f^2) error in abs(S), 3.8e-3 at 20 GHz for a 0.5 mm cell at
    Courant 0.95, which had been mistaken for a port discretisation error.
    """
    x = np.pi * np.asarray(frequencies, dtype=np.float64) * dt
    out: ComplexArray = np.cos(x).astype(np.complex128)
    return out


def sparams(model: Model, res: RunResult, *, reciprocal: bool = False) -> ComplexArray:
    """Power-wave S-parameters ``S[i, j, f]`` of the ports.

    Batch member ``k`` drives port ``model.excited_indices()[k]``. Columns of ports that were not
    driven (``Model.excite``) are NaN, or with ``reciprocal=True`` filled from ``S[j, i]`` where
    that entry exists (reciprocal passive structures).
    """
    ports = model.ports
    n = len(ports)
    if n == 0:
        raise ModelError("FDTD_NO_PORTS", "S-parameters need at least one port")
    cols = model.excited_indices()
    nf = res.source_dft.shape[1]
    fac = port_voltage_factor(res.dt, model.frequencies)
    s = np.full((n, n, nf), np.nan + 0j, np.complex128)
    for k, j in enumerate(cols):  # batch member k drives port j
        v = np.zeros((n, nf), np.complex128)
        for i in range(n):
            v[i] = res.probes[f"V:{ports[i].name}"][k] * fac
        a_j = res.source_dft[j] / (2.0 * np.sqrt(res.port_r[j]))
        for i in range(n):
            s[i, j] = (v[i] / np.sqrt(res.port_r[i]) - (a_j if i == j else 0.0)) / a_j
    if reciprocal:
        for j in range(n):
            if j in cols:
                continue
            for i in cols:
                s[i, j] = s[j, i]  # S_ij = S_ji; the driven column i holds S_ji
    return s
