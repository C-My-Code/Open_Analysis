# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""De-embedding: reference-plane shift from a measured gamma, and a 2x-thru hook (ADR-0010 s. 6)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt

if TYPE_CHECKING:
    from oa_fdtd.model import Model

FloatArray = npt.NDArray[np.float64]
ComplexArray = npt.NDArray[np.complex128]

# ``hook(S, frequencies) -> S'``: for example an ``oa_network`` IEEE 370 2x-thru de-embedder.
DeembedHook = Callable[[ComplexArray, FloatArray], ComplexArray]


def gamma_from_planes(
    v1: ComplexArray,
    v2: ComplexArray,
    v3: ComplexArray,
    spacing: float,
    spacing_b: float | None = None,
) -> ComplexArray:
    """Propagation constant [1/m] of a uniform line from the modal voltages at three planes
    (``v2`` in the middle, ``spacing`` between planes 1 and 2, ``spacing_b`` between 2 and 3).

    ``V(z) = A e^{-g z} + B e^{g z}`` gives ``cosh(g d) = (V1 + V3) / (2 V2)`` for equal spacings
    whatever the reflection, so the matched termination assumption of a two-plane ratio is not
    needed. Unequal spacings are solved by Newton iterations from that value. The branch has
    ``Re g >= 0``; the phase is unwrapped along frequency.
    """
    db = spacing if spacing_b is None else spacing_b
    dm = 0.5 * (spacing + db)
    c = (v1 + v3) / (2.0 * v2)
    g0 = np.arccosh(c.astype(np.complex128)) / dm
    g: ComplexArray = np.asarray(np.where(g0.real < 0, -g0, g0), dtype=np.complex128)
    if abs(db - spacing) > 1e-9 * spacing:
        for _ in range(8):
            g = _newton_step(g, v1, v2, v3, spacing, db)
    g = np.asarray(np.where(g.real < 0, -g, g), dtype=np.complex128)  # cosh is even: Re g >= 0
    g = np.where(g.imag < 0, np.conj(g), g)
    # arccosh returns Im in [0, pi]: unwrap beta d along frequency
    beta = np.unwrap(g.imag * dm) / dm
    return np.asarray(g.real + 1j * beta, dtype=np.complex128)


def smooth_gamma(
    gamma: ComplexArray, freqs: FloatArray, spacing: float, min_phase: float
) -> ComplexArray:
    """Replace the ill-conditioned low-frequency part of a measured gamma by a smooth fit.

    Where ``abs(Im g) * spacing < min_phase`` the three-plane formula divides noise by a vanishing
    phase. The well-conditioned points are fitted with ``alpha = a1 sqrt(f) + a2 f + a3 f^2`` and
    ``beta = b1 f + b2 f^2 + b3 f^3`` (conductor and dielectric loss, weak dispersion) and the fit
    is used below the conditioned band. The phase between planes is not needed to be accurate there
    (the amplitudes depend on gamma only through its small phase).
    """
    ok = np.abs(gamma.imag) * spacing >= min_phase
    if ok.sum() < 6 or ok.all():
        return gamma
    f = np.asarray(freqs, dtype=np.float64)
    fn = f / float(np.max(f))
    a_basis = np.column_stack([np.sqrt(fn), fn, fn**2])
    b_basis = np.column_stack([fn, fn**2, fn**3])
    ca, *_ = np.linalg.lstsq(a_basis[ok], gamma.real[ok], rcond=None)
    cb, *_ = np.linalg.lstsq(b_basis[ok], gamma.imag[ok], rcond=None)
    fit = a_basis @ ca + 1j * (b_basis @ cb)
    return np.asarray(np.where(ok, gamma, fit), dtype=np.complex128)


def _unequal_residual(
    g: ComplexArray, v1: ComplexArray, v2: ComplexArray, v3: ComplexArray, da: float, db: float
) -> ComplexArray:
    ca, sa, cb, sb = np.cosh(g * da), np.sinh(g * da), np.cosh(g * db), np.sinh(g * db)
    return np.asarray((v1 - v2 * ca) * sb + (v3 - v2 * cb) * sa, dtype=np.complex128)


def _newton_step(
    g: ComplexArray, v1: ComplexArray, v2: ComplexArray, v3: ComplexArray, da: float, db: float
) -> ComplexArray:
    h = 1e-6 * np.maximum(np.abs(g), 1.0)
    f0 = _unequal_residual(g, v1, v2, v3, da, db)
    df = (_unequal_residual(g + h, v1, v2, v3, da, db) - f0) / h
    ok = np.abs(df) > 0
    return np.asarray(np.where(ok, g - f0 / np.where(ok, df, 1.0), g), dtype=np.complex128)


def shift_reference(
    s: ComplexArray, gamma: Sequence[ComplexArray] | ComplexArray, lengths: Sequence[float]
) -> ComplexArray:
    """Move the reference plane of port ``i`` inwards by ``lengths[i]`` [m].

    ``S'_ij = S_ij exp(g_i L_i) exp(g_j L_j)`` (the line section between the old and the new plane
    is removed; the reference impedance is unchanged). ``gamma`` is one array for all ports or one
    per port; a negative length moves the plane outwards.
    """
    n = s.shape[0]
    gam = [np.asarray(gamma)] * n if np.ndim(gamma[0]) == 0 else [np.asarray(g) for g in gamma]
    fac = [np.exp(gam[i] * lengths[i]) for i in range(n)]
    out = np.empty_like(s)
    for i in range(n):
        for j in range(n):
            out[i, j] = s[i, j] * fac[i] * fac[j]
    return out


def apply_hook(s: ComplexArray, freqs: FloatArray, hook: DeembedHook) -> ComplexArray:
    """Run a fixture de-embedder (2x-thru through ``oa_network``) on ``S[i, j, f]``."""
    return hook(s, freqs)


@dataclass(frozen=True)
class PlaneSet:
    """Three modal probe planes of one port, outer to inner, equally spaced along the line.

    ``spacing`` [m] separates them; ``ref`` [m] is the signed distance from the middle plane to the
    reference plane (positive inwards, negative towards the port).
    """

    port: str
    probes: tuple[str, str, str]
    spacing: float
    ref: float
    spacing_b: float | None = None  # inner gap when unequal


def add_wave_planes(
    model: Model, port: str, offset: float, spacing_cells: int = 16, max_phase: float = 2.2
) -> PlaneSet:
    """Add three modal probe planes inside the line behind ``port`` and return their description.

    The outer plane is at least ``offset`` [m] from the port plane; the planes are the widest
    equal spacing of at most ``spacing_cells`` cells that the mesh offers (a uniform stretch),
    capped so that they stay in the half of the line next to the port and so that the phase
    between them stays below ``max_phase`` [rad] at the highest frequency (the cosh formula
    aliases at pi). The phase between planes
    should exceed about 0.3 rad for a well-conditioned gamma (:func:`wave_sparams`).
    """
    from oa_fdtd.model import ModelError, ModePort

    mp = next((p for p in model.ports if isinstance(p, ModePort) and p.name == port), None)
    if mp is None:
        raise ModelError("FDTD_BAD_PORT", f"no mode port named {port!r}")
    lines = model.grid.lines[mp.axis]
    i_port = int(np.argmin(np.abs(lines - mp.position)))
    step = mp.direction
    eps_max = max(
        [1.0] + [r.eps_r + sum(d for d, _ in r.poles) for r in model.regions if not r.pec]
    )
    f_top = float(np.max(model.frequencies))
    d_target = max_phase * 299792458.0 / (2.0 * np.pi * f_top * float(np.sqrt(eps_max)))
    found: tuple[list[int], float, float] | None = None
    depth = 0.45 * float(lines[-1] - lines[0])
    for k in range(spacing_cells, 0, -1):  # the widest equal spacing gives the best conditioning
        for i_mid in range(i_port + step * 2, i_port + step * lines.size, step):
            if not 0 <= i_mid < lines.size:
                break
            idx = [i_mid - step * k, i_mid, i_mid + step * k]
            if min(idx) < 0 or max(idx) >= lines.size:
                continue
            if (float(lines[idx[0]]) - mp.position) * step < offset:
                continue
            if abs(float(lines[idx[2]]) - mp.position) > depth:
                break
            d1 = abs(float(lines[idx[1]] - lines[idx[0]]))
            d2 = abs(float(lines[idx[2]] - lines[idx[1]]))
            if d1 > d_target:
                break
            if abs(d1 - d2) <= 0.1 * d1:
                found = (idx, d1, d2)
                break
        if found:
            break
    if found is None:
        raise ModelError(
            "FDTD_UNEQUAL_PLANES",
            f"no three equally spaced planes behind {port} at least {offset:.3e} m from it",
            "Use a uniform stretch of the mesh along the line.",
        )
    idx, d1, d2 = found
    pos = [float(lines[i]) for i in idx]
    names = tuple(f"W:{port}:{k}" for k in range(3))
    for nm, x in zip(names, pos, strict=True):
        model.mode_probes.append(replace(mp, name=nm, position=x))
    ref = abs(float(lines[i_port]) - pos[1])
    return PlaneSet(port, tuple(f"V:{nm}" for nm in names), d1, -ref, d2)  # type: ignore[arg-type]


def wave_sparams(
    probes: dict[str, ComplexArray],
    planes: Sequence[PlaneSet],
    port_r: FloatArray,
    s_port: ComplexArray | None = None,
    min_phase: float = 0.3,
    freqs: FloatArray | None = None,
    floor_phase: float = 0.04,
) -> tuple[ComplexArray, list[ComplexArray]]:
    """S-matrix from modal waves on probe planes (a de-embedded, plane-based extraction).

    For every excitation ``j`` (batch member ``j``) each port's three modal voltages give the
    propagation constant (:func:`gamma_from_planes`) and the waves into and out of the structure
    at the reference plane, ``V = A e^{-g z} + B e^{g z}`` with ``z`` inwards. Power waves
    ``a = A / sqrt(R)``, ``b = B / sqrt(R)``; ``S_ij = b_i / a_j`` of the driven port. The loss and
    reflection of the port region itself cancel because ``a_j`` is measured, not assumed. Returns
    ``(S[i, j, f], [gamma_i])``.

    Where the phase between the planes, ``abs(Im g) d``, is below ``min_phase`` gamma is
    ill-conditioned (differences of nearly equal voltages). With ``freqs`` given it is replaced
    there by a smooth fit (:func:`smooth_gamma`) and the plane waves are kept, except that below
    ``floor_phase`` (about 0.5 GHz on the B-EM-01 thru) the reflections ``S_ii`` come from
    ``s_port``: the plane extraction divides by the vanishing phase there (S11 -28 dB at 0.2 GHz
    against -37 dB from the port), while the plane transmission stays far better than the port's
    (0.009 dB against 0.096 dB IL). Without ``freqs`` ``s_port`` replaces all of ``S`` below
    ``min_phase``.
    """
    n = len(planes)
    nf = probes[planes[0].probes[0]].shape[1]
    s = np.zeros((n, n, nf), np.complex128)
    gammas: list[ComplexArray] = []
    a = np.zeros((n, n, nf), np.complex128)
    b = np.zeros((n, n, nf), np.complex128)
    for i, pl in enumerate(planes):
        for j in range(n):
            v1, v2, v3 = (probes[nm][j] for nm in pl.probes)
            db = pl.spacing if pl.spacing_b is None else pl.spacing_b
            g = gamma_from_planes(v1, v2, v3, pl.spacing, db)
            if freqs is not None:
                g = smooth_gamma(g, freqs, pl.spacing, min_phase)
            if j == 0:
                gammas.append(g)
            # amplitudes at the middle plane (z = 0)
            d_mid = (v1 - v2 * np.cosh(g * pl.spacing)) / np.sinh(g * pl.spacing)  # A - B
            a_mid = 0.5 * (v2 + d_mid)
            b_mid = 0.5 * (v2 - d_mid)
            a[i, j] = a_mid * np.exp(-g * pl.ref) / np.sqrt(port_r[i])
            b[i, j] = b_mid * np.exp(g * pl.ref) / np.sqrt(port_r[i])
    # ``a[i, j]`` / ``b[i, j]``: incident / outgoing wave at port i when port j is driven. The
    # undriven ports carry the re-reflection of their own sheet (a few percent), so the exact
    # relation is b = S a for the N runs together: S = B A^-1 (the port terminations drop out).
    for k in range(nf):
        am, bm = a[:, :, k], b[:, :, k]
        diag = np.abs(np.diag(am))
        off = np.abs(am - np.diag(np.diag(am))).max() if n > 1 else 0.0
        if n == 1 or off > 0.3 * diag.min() or not np.all(np.isfinite(am)):
            s[:, :, k] = bm / np.diag(am)[None, :]  # ill-conditioned: driven-wave normalisation
        else:
            s[:, :, k] = np.linalg.solve(am.T, bm.T).T
    if s_port is not None:
        thr = min_phase if freqs is None else floor_phase
        weak = np.zeros(nf, dtype=bool)
        for pl, g in zip(planes, gammas, strict=True):
            weak |= np.abs(g.imag) * pl.spacing < thr
        if freqs is None:
            s[:, :, weak] = s_port[:, :, weak]
        else:
            for i in range(n):
                s[i, i, weak] = s_port[i, i, weak]
            # a plane S that violates passivity is noise (a low-energy start of the pulse spectrum
            # divided by a vanishing phase): the port S is the safe estimate there
            noisy = np.abs(s).max(axis=(0, 1)) > 1.02
            s[:, :, noisy] = s_port[:, :, noisy]
    return s, gammas
