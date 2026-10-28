# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""S-parameter quality gate (interfaces.md section 4.5, research 03 section 2.5).

Metric provenance
-----------------
The metrics are *implemented from the equations of IEEE 370-2020 as described in the research
notes*, in the spirit of the standard's PQM / RQM / CQM, **not** ported from the IEEE 370
reference code (its license was not verifiable offline; see the WP-106 change request). They are
therefore calibrated to the same 0-100 percent scale and thresholds but are not bit-identical to
the reference implementation:

``PQM = 100 (1 - mean_f max(0, sigma_max(S(f)) - 1 - tol))``
    sigma_max is the largest singular value of the S-matrix (a passive network has sigma_max <= 1).
``RQM = 100 (1 - mean_f sum_{i!=j} |S_ij - S_ji| / (n (n - 1)))``
    1-ports are reciprocal by definition (100).
``CQM = 100 (1 - K * r_nc)``
    ``r_nc`` is the share of the L1 norm of the impulse response h(t) lying at negative time
    (more than 1/f_max before t = 0). h(t) comes from a delay-compensated resampling of S onto a
    uniform grid of step <= ``max_df_fraction * f_max`` with a DC point, a cosine taper over the
    top quarter of the band, and an inverse real FFT. ``K`` (``CQM_SCALE`` = 10) maps the small
    non-causal fractions of physical lossy lines onto the 80 %% acceptance threshold; it is an
    implementation choice, not a value from the standard.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import skrf
from numpy.typing import NDArray

from oa_results.quality import QualityCriteria, QualityReport

__all__ = [
    "CQM_SCALE",
    "QualityCriteria",
    "QualityReport",
    "VFModel",
    "assess",
    "enforce_passivity",
    "gate",
    "make_dc",
]

CQM_SCALE = 10.0
_GUARD = 2.0  # guard before t = 0, in units of 1/f_max
_TAPER = 0.5
_MAX_GRID = 8192
_PASSIVITY_FAIL = 1e-3

CArr = NDArray[np.complex128]
FArr = NDArray[np.float64]


@dataclass(frozen=True)
class VFModel:
    """Summary of a vector-fit macromodel (the fitted object is kept in ``vf``)."""

    n_poles: int
    rms_error: float
    passive: bool
    vf: Any = None


# ----------------------------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------------------------
def _sv_max(s: CArr) -> FArr:
    return np.asarray(np.linalg.svd(s, compute_uv=False)[:, 0], dtype=np.float64)


def _dc_status(f: FArr) -> Literal["present", "extrapolated", "missing"]:
    if f[0] <= 1e-9 * max(f[-1], 1.0):
        return "present"
    if f.size >= 3 and f[0] <= 0.1 * f[-1]:
        return "extrapolated"
    return "missing"


def _delay(f: FArr, s: CArr) -> float:
    """Dominant delay [s] from the phase slope of the strongest transmission entry."""
    n = s.shape[1]
    if n < 2 or f.size < 3:
        return 0.0
    mag = np.abs(s).mean(axis=0)
    np.fill_diagonal(mag, 0.0)
    i, j = np.unravel_index(int(np.argmax(mag)), mag.shape)
    ph = np.unwrap(np.angle(s[:, i, j]))
    slope = np.polyfit(f, ph, 1)[0]
    return float(max(-slope / (2.0 * math.pi), 0.0))


def _dc_values(f: FArr, s: CArr) -> CArr:
    """S(0) by least squares on the first points: Re = a + b f^2 (even), Im = c f (odd)."""
    m = min(6, f.size)
    ff = f[:m] / f[-1]
    out = np.empty(s.shape[1:], dtype=np.complex128)
    ae = np.stack([np.ones(m), ff**2], axis=1)
    for i in range(s.shape[1]):
        for j in range(s.shape[2]):
            re = np.linalg.lstsq(ae, s[:m, i, j].real, rcond=None)[0][0]
            out[i, j] = re
    return out


def _uniform_response(net: skrf.Network, max_df_fraction: float) -> tuple[FArr, CArr]:
    """Resample S onto a uniform grid starting at DC (delay compensated)."""
    f = np.asarray(net.f, dtype=float)
    s = np.asarray(net.s, dtype=complex)
    fmax = float(f[-1])
    step = fmax * max_df_fraction
    if f.size > 1:
        step = min(step, float(np.median(np.diff(f))))
    n_pts = int(min(math.ceil(fmax / step), _MAX_GRID))
    fu = np.linspace(0.0, fmax, n_pts + 1)
    tau = _delay(f, s)
    comp = np.exp(2j * math.pi * f * tau)[:, None, None]
    sc = s * comp
    if f[0] > 1e-9 * fmax:
        f_all = np.concatenate([[0.0], f])
        sc = np.concatenate([_dc_values(f, sc)[None], sc], axis=0)
    else:
        f_all = f
    out = np.empty((fu.size, *s.shape[1:]), dtype=np.complex128)
    for i in range(s.shape[1]):
        for j in range(s.shape[2]):
            out[:, i, j] = np.interp(fu, f_all, sc[:, i, j].real) + 1j * np.interp(
                fu, f_all, sc[:, i, j].imag
            )
    out *= np.exp(-2j * math.pi * fu * tau)[:, None, None]
    return fu, out


def _cqm(net: skrf.Network, criteria: QualityCriteria) -> float:
    fu, su = _uniform_response(net, criteria.max_df_fraction)
    nf = fu.size - 1
    fmax = fu[-1]
    taper = np.ones(fu.size)
    k0 = int(_TAPER * nf)
    taper[k0:] = np.cos(0.5 * math.pi * (np.arange(k0, fu.size) - k0) / max(nf - k0, 1)) ** 2
    total = 0.0
    neg = 0.0
    n_t = 2 * nf
    dt = 1.0 / (2.0 * fmax)
    guard = max(math.ceil(_GUARD / fmax / dt), 1)
    lo = n_t // 2
    hi = n_t - guard
    p = su.shape[1]
    for i in range(p):
        for j in range(i, p):
            h = np.fft.irfft(su[:, i, j] * taper, n=n_t)
            a = np.abs(h)
            total += float(a.sum())
            neg += float(a[lo:hi].sum())
    if total <= 0.0:
        return 100.0
    return float(max(0.0, 100.0 * (1.0 - CQM_SCALE * neg / total)))


# ----------------------------------------------------------------------------------------------
# public API
# ----------------------------------------------------------------------------------------------
def assess(net: skrf.Network, criteria: QualityCriteria | None = None) -> QualityReport:
    """Measure passivity, reciprocity, causality and DC availability (no modification)."""
    crit = criteria or QualityCriteria()
    f = np.asarray(net.f, dtype=float)
    s = np.asarray(net.s, dtype=complex)
    n = s.shape[1]
    notes: list[str] = []

    sv = _sv_max(s)
    viol = np.maximum(sv - 1.0 - crit.passivity_tol, 0.0)
    pqm = float(100.0 * (1.0 - viol.mean()))
    k = int(np.argmax(sv))
    max_sv = float(sv[k])
    worst = float(f[k]) if max_sv > 1.0 + crit.passivity_tol and f[k] > 0.0 else None

    if n > 1:
        d = np.abs(s - np.transpose(s, (0, 2, 1)))
        rm = d.sum(axis=(1, 2)) / (n * (n - 1))
        rqm = float(100.0 * (1.0 - rm.mean()))
        rec_err = float(d.max())
    else:
        rqm, rec_err = 100.0, 0.0

    dc = _dc_status(f)
    if dc == "missing":
        notes.append("DC point missing and not extrapolatable (f_min > 10% of f_max)")
        cqm = 100.0
        notes.append("CQM not evaluated without a DC point")
    else:
        if dc == "extrapolated":
            notes.append("DC extrapolated for the causality evaluation")
        cqm = _cqm(net, crit)

    verdict = _verdict(max_sv, pqm, rqm, cqm, dc, crit, fixes=())
    return QualityReport(
        verdict=verdict,
        pqm=pqm,
        rqm=rqm,
        cqm=cqm,
        max_singular_value=max_sv,
        worst_passivity_freq=worst,
        reciprocity_max_err=rec_err,
        dc=dc,
        f_min=float(f[0]),
        f_max=float(f[-1]),
        n_freqs=int(f.size),
        criteria=crit,
        notes=tuple(notes),
    )


def _verdict(
    max_sv: float,
    pqm: float,
    rqm: float,
    cqm: float,
    dc: str,
    crit: QualityCriteria,
    fixes: tuple[str, ...],
) -> Literal["pass", "warn", "fail"]:
    dc_ok = (
        crit.require_dc == "none"
        or (crit.require_dc == "present" and dc == "present")
        or (crit.require_dc == "extrapolatable" and dc != "missing")
    )
    if max_sv > 1.0 + _PASSIVITY_FAIL or pqm < crit.pqm_min or rqm < crit.rqm_min or not dc_ok:
        return "fail"
    if cqm < crit.cqm_min or fixes:
        return "warn"
    return "pass"


def _fit(net: skrf.Network, n_cmplx: int) -> tuple[Any, int]:
    vf = skrf.vectorFitting.VectorFitting(net)
    vf.vector_fit(n_poles_real=2, n_poles_cmplx=n_cmplx)
    return vf, 2 + 2 * n_cmplx


_VF_ORDERS = (6, 8, 10, 12, 16, 24)


def make_dc(net: skrf.Network, method: Literal["skrf", "rational"] = "skrf") -> skrf.Network:
    """Return ``net`` with a DC point (f = 0) prepended. No-op if DC is already present.

    ``skrf`` uses ``Network.extrapolate_to_dc``; ``rational`` evaluates a vector-fit model at 0.
    """
    f = np.asarray(net.f, dtype=float)
    if f[0] <= 1e-9 * f[-1]:
        return net.copy()
    if method == "skrf":
        out: skrf.Network = net.extrapolate_to_dc(kind="linear")
        return out
    vf = _fit(net, 12)[0]
    n = net.nports
    dc = np.empty((1, n, n), dtype=complex)
    for i in range(n):
        for j in range(n):
            dc[0, i, j] = vf.get_model_response(i, j, freqs=[0.0])[0].real
    s = np.concatenate([dc, net.s], axis=0)
    freq = skrf.Frequency.from_f(np.concatenate([[0.0], f]), unit="hz")
    return skrf.Network(frequency=freq, s=s, z0=net.z0[0, :], name=net.name)


def enforce_passivity(
    net: skrf.Network, *, n_poles: int | None = None
) -> tuple[skrf.Network, VFModel]:
    """Vector-fit ``net`` (scikit-rf) and enforce passivity on the macromodel.

    With ``n_poles=None`` the number of complex pole pairs is raised (6 ... 24) until the
    perturbed model is passive. Returns the network on the original grid and a :class:`VFModel`
    (``passive`` is False if no order succeeded; the last attempt is returned).
    """
    f = np.asarray(net.f, dtype=float)
    n = net.nports
    orders = _VF_ORDERS if n_poles is None else (max((n_poles - 2) // 2, 1),)
    vf: Any = None
    order = 0
    for n_c in orders:
        vf, order = _fit(net, n_c)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            vf.passivity_enforce(n_samples=1000, f_max=float(f[-1]) * 2.0, preserve_dc=False)
        if vf.is_passive():
            break
    assert vf is not None
    s = np.empty((f.size, n, n), dtype=complex)
    for i in range(n):
        for j in range(n):
            s[:, i, j] = vf.get_model_response(i, j, freqs=f)
    out = skrf.Network(frequency=net.frequency, s=s, z0=net.z0[0, :], name=net.name)
    return out, VFModel(
        n_poles=order,
        rms_error=float(np.max(vf.get_rms_error())),
        passive=bool(vf.is_passive()),
        vf=vf,
    )


def gate(
    net: skrf.Network, criteria: QualityCriteria | None = None, *, fix: bool = False
) -> tuple[skrf.Network, QualityReport]:
    """Mandatory quality gate: assess, optionally repair (DC, passivity), and re-assess.

    Fixes: ``dc_extrapolated`` and ``passivity_enforced(vf, N poles)``. Reciprocity and
    causality are never "fixed"; they stay in the report.
    """
    crit = criteria or QualityCriteria()
    rep = assess(net, crit)
    if not fix:
        return net, rep
    fixes: list[str] = []
    cur = net
    if rep.dc == "extrapolated" and crit.require_dc != "none":
        cur = make_dc(cur)
        fixes.append("dc_extrapolated")
    if rep.max_singular_value > 1.0 + crit.passivity_tol:
        cur, model = enforce_passivity(cur)
        fixes.append(f"passivity_enforced(vf, {model.n_poles} poles)")
    if not fixes:
        return net, rep
    after = assess(cur, crit)
    verdict = _verdict(
        after.max_singular_value,
        after.pqm,
        after.rqm,
        after.cqm,
        after.dc,
        crit,
        tuple(fixes),
    )
    return cur, after.model_copy(update={"verdict": verdict, "fixes_applied": tuple(fixes)})
