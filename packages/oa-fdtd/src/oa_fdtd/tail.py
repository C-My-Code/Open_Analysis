# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Matrix-pencil tail extrapolation of a truncated probe DFT (ADR-0010 section 6).

A run that stops at its step cap (high-Q structures) leaves a tail ``y(t), t > T`` that the running
DFT misses. The last ``window`` samples are fitted by ``y_n = sum_m c_m z_m^n`` (matrix pencil on
the decimated series, amplitudes by least squares), and the DFT of the continuation from sample
``S`` on is added in closed form,
``sum_{n >= S} y_n e^{-j w t_n} dt
= sum_m c_m e^{(s_m - j w)(t_S - t_0)} dt / (1 - e^{(s_m - j w) dt})``
with ``s_m = ln(z_m) / (d dt)``. Poles that do not decay (``Re s >= 0``) or whose frequency lies
far above the analysis band are dropped and counted.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
ComplexArray = npt.NDArray[np.complex128]

__all__ = ["TailFit", "extrapolate_dft", "fit_tail"]


@dataclass(frozen=True)
class TailFit:
    """Complex exponentials ``y(t) = sum c_m exp(s_m (t - t_ref))`` of the series tail."""

    s: ComplexArray  # [1/s]
    c: ComplexArray
    t_ref: float  # [s] time of the first fitted sample
    n_dropped: int  # poles removed (not decaying or out of band)
    residual: float  # relative LS misfit on the window


def fit_tail(
    y: FloatArray,
    dt: float,
    t_first: float,
    *,
    f_band: float,
    window: int = 6000,
    max_poles: int = 40,
    sv_tol: float = 1e-4,
    samples: int = 700,
) -> TailFit | None:
    """Fit the last ``window`` samples of ``y`` (sample ``n`` at ``t_first + n dt``).

    The window is decimated to about ``samples`` points, but never coarser than 3 samples per
    period of ``2 f_band``. Returns ``None`` when the tail is below numerical noise.
    """
    x = np.asarray(y, dtype=np.float64)
    w = min(window, x.size)
    seg = x[x.size - w :]
    scale = float(np.max(np.abs(x))) if x.size else 0.0
    if scale == 0.0 or float(np.max(np.abs(seg))) < 1e-9 * scale:
        return None
    d_max = max(int(1.0 / (6.0 * f_band * dt)), 1)
    d = int(np.clip(w // samples, 1, d_max))
    seg = seg[::-1][::d][::-1]  # keep the last sample
    n = seg.size
    if n < 16:
        return None
    t0 = t_first + (x.size - 1 - d * (n - 1)) * dt
    h = d * dt
    ell = n // 3
    idx = np.arange(n - ell)[:, None] + np.arange(ell + 1)[None, :]
    _, sv, vh = np.linalg.svd(seg[idx], full_matrices=False)
    m = min(max_poles, int(np.count_nonzero(sv > sv_tol * sv[0])))
    if m < 1:
        return None
    v = vh[:m].conj().T
    z = np.linalg.eigvals(np.linalg.pinv(v[:-1]) @ v[1:])
    s = np.asarray(np.log(z.astype(np.complex128)) / h, dtype=np.complex128)
    keep = (s.real < -1e-3 / (n * h)) & (np.abs(s.imag) < 2.0 * np.pi * 2.0 * f_band)
    dropped = int(s.size - keep.sum())
    s = s[keep]
    if s.size == 0:
        return None
    vand = np.exp(np.outer(np.arange(n) * h, s))
    c, *_ = np.linalg.lstsq(vand, seg.astype(np.complex128), rcond=None)
    res = float(np.linalg.norm(vand @ c - seg) / max(np.linalg.norm(seg), 1e-300))
    return TailFit(s=s, c=c.astype(np.complex128), t_ref=t0, n_dropped=dropped, residual=res)


def extrapolate_dft(fit: TailFit, dt: float, t_next: float, freqs: FloatArray) -> ComplexArray:
    """DFT contribution ``sum_{n >= S} y_n e^{-j w t_n} dt`` of the fitted continuation.

    ``t_next`` is the time stamp of the first sample not yet in the running DFT.
    """
    w = 2.0 * np.pi * np.asarray(freqs, dtype=np.float64)
    a = fit.s[None, :] - 1j * w[:, None]  # (F, M)
    num = fit.c[None, :] * np.exp(a * (t_next - fit.t_ref)) * dt
    out: ComplexArray = np.sum(num / (1.0 - np.exp(a * dt)), axis=1)
    return out
