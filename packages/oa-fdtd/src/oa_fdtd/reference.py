# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""NumPy reference engine: the same equations as the CUDA kernels, vectorized slices.

It is the test oracle and the CPU fallback (ADR-0010 section 8). ``dtype`` is float64 by default;
float32 reproduces the GPU precision on the CPU.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt

from oa_fdtd.grid import EPS0, MU0
from oa_fdtd.model import Compiled
from oa_fdtd.pml import E_TERMS, H_TERMS

FloatArray = npt.NDArray[np.float64]
Rng = tuple[tuple[int, int], tuple[int, int], tuple[int, int]]


def _view(f: Any, rng: Rng, axis: int = -1, shift: int = 0) -> Any:
    """Slice of the ``(B, z, y, x)`` array ``f`` over ``rng`` (x, y, z), shifted along ``axis``."""
    sl = []
    for a in (2, 1, 0):
        lo, hi = rng[a]
        s = shift if a == axis else 0
        sl.append(slice(lo + s, hi + s))
    return f[(slice(None), *sl)]


def _coef(arr: FloatArray, axis: int, rng: Rng) -> FloatArray:
    shape = [1, 1, 1, 1]
    lo, hi = rng[axis]
    shape[3 - axis] = hi - lo
    return arr[lo:hi].reshape(shape)


class NumpyEngine:
    """Leapfrog Yee update on a :class:`~oa_fdtd.model.Compiled` problem."""

    name = "numpy"

    def __init__(self, c: Compiled, dtype: str = "float64", record_series: bool = False) -> None:
        self.c = c
        self.record_series = record_series
        self._series: dict[str, list[FloatArray]] = {"E": [], "H": []}
        g = c.grid
        self.dtype = np.dtype(dtype)
        dt = self.dtype
        b = c.batch
        shape = (b, *g.shape)
        self.E = [np.zeros(shape, dt) for _ in range(3)]
        self.H = [np.zeros(shape, dt) for _ in range(3)]
        self.ca = [c.ca_tab[i][None].astype(dt) for i in c.idx]
        self.cb = [c.cb_tab[i][None].astype(dt) for i in c.idx]
        self.dtmu = dt.type(c.dt / MU0)
        self.ip = [g.inv_primal(a).astype(dt) for a in range(3)]
        self.idd = [g.inv_dual(a).astype(dt) for a in range(3)]
        full: Rng = tuple((1, g.n[a] + 2) for a in range(3))  # type: ignore[assignment]
        self.full = full
        self.psi = [np.zeros((b, *corr.shape), dt) for corr in c.pml.corrections]
        self.pmc = [(a, s) for a in range(3) for s in (0, 1) if c.walls[2 * a + s] == "pmc"]
        self.step_count = 0
        self.t_e = [p for p in c.probes if p["kind"] == "E"]
        self.t_h = [p for p in c.probes if p["kind"] == "H"]
        nf = c.frequencies.size
        self.acc = {
            "E": np.zeros((nf, b, len(self.t_e)), np.complex128),
            "H": np.zeros((nf, b, len(self.t_h)), np.complex128),
        }
        self.omega = 2.0 * np.pi * c.frequencies

    # ---- updates
    def _h_update(self) -> None:
        g = self.c.grid
        for ci in range(3):
            rng = g.valid("H", ci)
            acc = None
            for c_, a, b_, sign in H_TERMS:
                if c_ != ci:
                    continue
                d = (_view(self.E[b_], rng, a, 1) - _view(self.E[b_], rng)) * _coef(self.ip[a], a, rng)
                term = d if sign > 0 else -d
                acc = term if acc is None else acc + term
            _view(self.H[ci], rng)[...] += self.dtmu * acc

    def _e_update(self) -> None:
        for ci in range(3):
            rng = self.full
            acc = None
            for c_, a, b_, sign in E_TERMS:
                if c_ != ci:
                    continue
                d = (_view(self.H[b_], rng) - _view(self.H[b_], rng, a, -1)) * _coef(self.idd[a], a, rng)
                term = d if sign > 0 else -d
                acc = term if acc is None else acc + term
            e = _view(self.E[ci], rng)
            e[...] = _view(self.ca[ci], rng) * e + _view(self.cb[ci], rng) * acc

    def _pml(self, kind: str) -> None:
        c = self.c
        for corr, psi in zip(c.pml.corrections, self.psi, strict=True):
            if corr.kind != kind:
                continue
            co = c.pml.coeffs[corr.a]
            rng = corr.box
            a = corr.a
            if kind == "E":
                d = (_view(self.H[corr.b], rng) - _view(self.H[corr.b], rng, a, -1)) * _coef(
                    self.idd[a], a, rng
                )
                b_, c_, k_ = co.eb, co.ec, co.ek
            else:
                d = (_view(self.E[corr.b], rng, a, 1) - _view(self.E[corr.b], rng)) * _coef(
                    self.ip[a], a, rng
                )
                b_, c_, k_ = co.hb, co.hc, co.hk
            dt = self.dtype
            psi *= _coef(b_.astype(dt), a, rng)
            psi += _coef(c_.astype(dt), a, rng) * d
            upd = _coef(k_.astype(dt), a, rng) * d + psi
            if kind == "E":
                _view(self.E[corr.c], rng)[...] += corr.sign * _view(self.cb[corr.c], rng) * upd
            else:
                _view(self.H[corr.c], rng)[...] += corr.sign * self.dtmu * upd

    def _ghosts(self) -> None:
        g = self.c.grid
        for a, s in self.pmc:
            for comp in range(3):
                if comp == a:
                    continue
                h = self.H[comp]
                ax = 3 - a  # array axis of the (B, z, y, x) layout
                dst, src = (0, 1) if s == 0 else (g.n[a] + 1, g.n[a])
                d_sl = [slice(None)] * 4
                s_sl = [slice(None)] * 4
                d_sl[ax], s_sl[ax] = dst, src
                h[tuple(d_sl)] = -h[tuple(s_sl)]

    def _inject(self, step: int) -> None:
        c = self.c
        for ci, ch in enumerate(c.channels):
            w = float(ch["waveform"](np.array([(step + 0.5) * c.dt]))[0])
            f = self.E[ch["comp"]].reshape(c.batch, -1)
            f[:, ch["flat"]] += (
                (c.drive[:, ci : ci + 1] * w) * ch["amps"][None, :]
            ).astype(self.dtype)

    def _record(self, kind: str, t: float) -> None:
        probes = self.t_e if kind == "E" else self.t_h
        if not probes:
            return
        arrs = self.E if kind == "E" else self.H
        b = self.c.batch
        vals = np.empty((b, len(probes)))
        for pi, p in enumerate(probes):
            f = arrs[p["comp"]].reshape(b, -1)
            vals[:, pi] = (f[:, p["flat"]].astype(np.float64) * p["weights"][None, :]).sum(axis=1)
        if self.record_series:
            self._series[kind].append(vals)
        ph = np.exp(-1j * self.omega * t) * self.c.dt
        self.acc[kind] += ph[:, None, None] * vals[None, :, :]

    def step(self, n: int = 1) -> None:
        dt = self.c.dt
        for _ in range(n):
            k = self.step_count
            self._h_update()
            self._pml("H")
            self._ghosts()
            self._record("H", (k + 0.5) * dt)
            self._e_update()
            self._pml("E")
            self._inject(k)
            self._record("E", (k + 1) * dt)
            self.step_count += 1

    # ---- observables
    def energy(self) -> float:
        """``1/2 sum eps E^2 V + 1/2 mu0 sum H^2 V`` [J], float64."""
        g, c = self.c.grid, self.c
        total = 0.0
        for ci in range(3):
            w = self._weights("E", ci)
            eps = (c.eps_tab[c.idx[ci]] * EPS0)[None]
            total += 0.5 * float(np.sum(eps * w * self.E[ci].astype(np.float64) ** 2))
            wh = self._weights("H", ci)
            total += 0.5 * MU0 * float(np.sum(wh * self.H[ci].astype(np.float64) ** 2))
        del g
        return total

    def energy_exact(self, h_prev: list[FloatArray]) -> float:
        """Conserved leapfrog energy using ``H^{n-1/2} . H^{n+1/2}`` for the magnetic part."""
        c = self.c
        total = 0.0
        for ci in range(3):
            w = self._weights("E", ci)
            eps = (c.eps_tab[c.idx[ci]] * EPS0)[None]
            total += 0.5 * float(np.sum(eps * w * self.E[ci].astype(np.float64) ** 2))
            wh = self._weights("H", ci)
            total += 0.5 * MU0 * float(
                np.sum(wh * h_prev[ci].astype(np.float64) * self.H[ci].astype(np.float64))
            )
        return total

    def _weights(self, kind: str, ci: int) -> FloatArray:
        g = self.c.grid
        out = np.ones((1, 1, 1, 1))
        for a in range(3):
            node = (a != ci) if kind == "E" else (a == ci)
            w = g.node_weights(a) if node else g.cell_weights(a)
            shape = [1, 1, 1, 1]
            shape[3 - a] = w.size
            out = out * w.reshape(shape)
        return out

    def h_copy(self) -> list[FloatArray]:
        return [h.astype(np.float64) for h in self.H]

    def results(self) -> dict[str, FloatArray]:
        """Probe DFTs ``{name: complex (B, F)}`` [field units x s], float64 accumulators."""
        out: dict[str, FloatArray] = {}
        for kind, probes in (("E", self.t_e), ("H", self.t_h)):
            for pi, p in enumerate(probes):
                out[p["name"]] = self.acc[kind][:, :, pi].T.copy()  # type: ignore[assignment]
        return out

    def series(self) -> dict[str, FloatArray]:
        """Recorded probe time series ``{name: (B, steps)}`` (needs ``record_series=True``)."""
        out: dict[str, FloatArray] = {}
        for kind, probes in (("E", self.t_e), ("H", self.t_h)):
            if probes and self._series[kind]:
                arr = np.stack(self._series[kind], axis=-1)  # (B, P, steps)
                for pi, p in enumerate(probes):
                    out[p["name"]] = arr[:, pi, :]
        return out

    def peak_bytes(self) -> int:
        return 0
