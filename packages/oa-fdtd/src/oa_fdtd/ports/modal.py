# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Quasi-TEM mode templates on a port plane (ADR-0010 section 6).

A 2-D electrostatic solve on the lattice of the port plane gives, for every signal conductor, the
potential ``phi_k`` (1 V on conductor ``k``, all other conductors and PEC walls at 0). Bilinear
(Q1) finite elements on the Yee lattice make the template exact on the Yee edges: the voltage of
an in-plane E edge is the potential difference of its two end nodes. The capacitance matrices
with and without the dielectric give the modes (``C0^-1 C u = eps_eff u``), their velocity and
their characteristic impedance ``Z = 1 / (v u^T C u)``.

This is a local solver written for the port plane. ADR-0010 names the ``oa_impedance`` 2-D FEM/BEM
for this step; it needs a polygon cross-section and a dependency from the engine side, which the
layering of ``oa_fdtd`` does not allow without a contract change. The interface is the same
(``eps`` per cell, conductor labels per node), so it can be swapped in.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import scipy.linalg  # type: ignore[import-untyped,unused-ignore]
import scipy.sparse as sp  # type: ignore[import-untyped,unused-ignore]
import scipy.sparse.linalg as spla  # type: ignore[import-untyped,unused-ignore]

FloatArray = npt.NDArray[np.float64]
C0 = 299792458.0  # [m/s]
EPS0 = 8.8541878128e-12  # [F/m]

_A = np.array([[2, -2, -1, 1], [-2, 2, 1, -1], [-1, 1, 2, -2], [1, -1, -2, 2]], np.float64) / 6.0
_B = np.array([[2, 1, -1, -2], [1, 2, -2, -1], [-1, -2, 2, 1], [-2, -1, 1, 2]], np.float64) / 6.0


@dataclass
class ModeSet:
    """Result of :func:`solve_modes`; arrays are indexed ``[mode, iu, iv]`` on the node lattice."""

    phi: FloatArray  # (n_modes, nu + 1, nv + 1) potential of the mode, max |conductor V| = 1 [V]
    z: FloatArray  # (n_modes,) characteristic impedance [ohm]
    eps_eff: FloatArray  # (n_modes,) effective permittivity [-]
    voltages: FloatArray  # (n_modes, n_signals) conductor voltage pattern [V]
    cap: FloatArray  # (n_signals, n_signals) capacitance per length [F/m]
    cap_air: FloatArray  # [F/m]


def _stiffness(u: FloatArray, v: FloatArray, eps: FloatArray) -> sp.csr_matrix:
    nu, nv = u.size - 1, v.size - 1
    hu, hv = np.diff(u), np.diff(v)
    iu, iv = np.meshgrid(np.arange(nu), np.arange(nv), indexing="ij")
    n00 = iu * (nv + 1) + iv
    nodes = np.stack([n00, n00 + (nv + 1), n00 + (nv + 1) + 1, n00 + 1], axis=-1)  # ccw
    ratio_a = (hv[None, :] / hu[:, None]) * eps  # (nu, nv)
    ratio_b = (hu[:, None] / hv[None, :]) * eps
    ke = ratio_a[..., None, None] * _A + ratio_b[..., None, None] * _B  # (nu, nv, 4, 4)
    rows = np.repeat(nodes[..., :, None], 4, axis=-1).ravel()
    cols = np.repeat(nodes[..., None, :], 4, axis=-2).ravel()
    n = (nu + 1) * (nv + 1)
    return sp.csr_matrix((ke.ravel(), (rows, cols)), shape=(n, n))


def _capacitance(
    k: sp.csr_matrix,
    labels: npt.NDArray[np.int64],
    n_sig: int,
) -> tuple[FloatArray, FloatArray]:
    """Potentials ``(n_sig, nu + 1, nv + 1)`` and the capacitance matrix [F/m] (eps in ``k`` is
    absolute)."""
    flat = labels.ravel()
    free = np.flatnonzero(flat < 0)
    phi = np.zeros((n_sig, flat.size))
    for s in range(n_sig):
        phi[s, flat == s + 1] = 1.0
    if free.size:
        kff = k[free][:, free].tocsc()
        lu = spla.splu(kff)
        fixed = np.flatnonzero(flat >= 0)
        for s in range(n_sig):
            rhs = -(k[free][:, fixed] @ phi[s, fixed])
            phi[s, free] = lu.solve(rhs)
    cap = np.array([[phi[a] @ (k @ phi[b]) for b in range(n_sig)] for a in range(n_sig)])
    return phi.reshape((n_sig, *labels.shape)), cap


def solve_modes(
    u: FloatArray,
    v: FloatArray,
    eps_cell: FloatArray,
    labels: npt.NDArray[np.int64],
    n_sig: int,
) -> ModeSet:
    """Quasi-TEM modes of ``n_sig`` signal conductors in the rectangle ``u x v``.

    ``eps_cell`` ``(nu, nv)`` [-]; ``labels`` ``(nu + 1, nv + 1)``: ``-1`` free node, ``0`` ground,
    ``k = 1 .. n_sig`` signal conductor ``k``. Free boundary nodes are PMC (natural condition).
    """
    if n_sig < 1:
        raise ValueError("a mode port needs at least one signal conductor")
    k_air = _stiffness(u, v, np.full_like(eps_cell, EPS0))
    k_die = _stiffness(u, v, eps_cell * EPS0)
    _, c0 = _capacitance(k_air, labels, n_sig)
    phi_c, c = _capacitance(k_die, labels, n_sig)
    if n_sig == 1:
        vecs = np.ones((1, 1))
        eeff = np.array([c[0, 0] / c0[0, 0]])
    else:
        w, vv = scipy.linalg.eigh(c, c0)
        order = np.argsort(w)
        eeff, vecs = w[order], vv[:, order].T
        for m in range(vecs.shape[0]):
            vecs[m] /= np.max(np.abs(vecs[m]))
            first = vecs[m][np.argmax(np.abs(vecs[m]) > 0.5)]
            if first < 0:
                vecs[m] = -vecs[m]
    vel = C0 / np.sqrt(eeff)
    z = np.array([1.0 / (vel[m] * (vecs[m] @ c @ vecs[m])) for m in range(len(eeff))])
    phi = np.tensordot(vecs, phi_c, axes=(1, 0))
    return ModeSet(phi=phi, z=z, eps_eff=eeff, voltages=vecs, cap=c, cap_air=c0)
