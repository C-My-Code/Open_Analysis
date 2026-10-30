# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPML with CFS stretching (ADR-0010 section 4).

Roden and Gedney, "Convolution PML (CPML): an efficient FDTD implementation of the CFS-PML for
arbitrary media", Microwave Opt. Technol. Lett. 27(5), 2000, 334-339. Per slab and per curl term the
derivative ``d`` is replaced by ``d / kappa + psi`` with ``psi <- b psi + c d``. Both engines apply
the correction after the bulk update, so the bulk kernels carry no PML code. The psi arrays exist
only inside the slabs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from oa_fdtd.grid import EPS0, ETA0, MU0, Grid

FloatArray = npt.NDArray[np.float64]
Box = tuple[tuple[int, int], tuple[int, int], tuple[int, int]]  # storage [lo, hi) for x, y, z

# E_c += sign * cb * d_a H_b   and   H_c += sign * (dt / mu0) * d_a E_b   (see grid.py)
E_TERMS = ((0, 1, 2, 1.0), (0, 2, 1, -1.0), (1, 2, 0, 1.0), (1, 0, 2, -1.0), (2, 0, 1, 1.0), (2, 1, 0, -1.0))
H_TERMS = ((0, 1, 2, -1.0), (0, 2, 1, 1.0), (1, 2, 0, -1.0), (1, 0, 2, 1.0), (2, 0, 1, -1.0), (2, 1, 0, 1.0))


@dataclass(frozen=True)
class CpmlSpec:
    """CPML parameters; the defaults are ADR-0010 section 4 (kappa_max is to be tuned)."""

    cells: int = 10  # [-] layer thickness
    m: float = 3.0  # [-] polynomial grading order
    kappa_max: float = 8.0  # [-]
    r0: float = 1e-8  # [-] design reflection at normal incidence (sets sigma_max)
    f_low: float = 1e8  # [Hz] alpha_max = 2 pi f_low eps0


@dataclass(frozen=True)
class Correction:
    """One curl-term correction: ``F_c += sign * k * (ek * d + psi)`` over ``box``."""

    kind: str  # "E" or "H"
    c: int  # corrected component axis
    a: int  # derivative axis (the slab axis)
    b: int  # source component axis
    sign: float
    box: Box

    @property
    def shape(self) -> tuple[int, int, int]:
        """psi shape ``(nz, ny, nx)`` without batch."""
        return tuple(self.box[ax][1] - self.box[ax][0] for ax in (2, 1, 0))  # type: ignore[return-value]


@dataclass(frozen=True)
class AxisCoeffs:
    """Per-axis 1-D coefficient arrays over the storage index, float64."""

    eb: FloatArray
    ec: FloatArray
    ek: FloatArray  # 1 / kappa - 1, at node indices (E-type)
    hb: FloatArray
    hc: FloatArray
    hk: FloatArray  # at cell indices (H-type)


@dataclass(frozen=True)
class Pml:
    corrections: tuple[Correction, ...]
    coeffs: dict[int, AxisCoeffs]


def _profile(depth: FloatArray, thick: float, spec: CpmlSpec, dt: float) -> tuple[FloatArray, ...]:
    rho = np.clip(depth / thick, 0.0, 1.0)
    sig_max = -(spec.m + 1.0) * math.log(spec.r0) / (2.0 * ETA0 * thick)
    sigma = sig_max * rho**spec.m
    kappa = 1.0 + (spec.kappa_max - 1.0) * rho**spec.m
    alpha = 2.0 * math.pi * spec.f_low * EPS0 * (1.0 - rho) * (rho > 0)
    b = np.exp(-(sigma / kappa + alpha) * dt / EPS0)
    den = kappa * (sigma + kappa * alpha)
    c = np.where(den > 0, sigma * (b - 1.0) / np.where(den > 0, den, 1.0), 0.0)
    b = np.where(rho > 0, b, 0.0)
    return b, c, 1.0 / kappa - 1.0


def build_pml(grid: Grid, walls: tuple[str, ...], spec: CpmlSpec, dt: float) -> Pml:
    """Coefficients and correction boxes for every wall flagged ``"cpml"``."""
    corrections: list[Correction] = []
    coeffs: dict[int, AxisCoeffs] = {}
    for a in range(3):
        sides = [s for s in (0, 1) if walls[2 * a + s] == "cpml"]
        if not sides:
            continue
        n = grid.n[a]
        npml = spec.cells
        if 2 * npml >= n:
            raise ValueError(f"axis {'xyz'[a]}: {n} cells cannot hold two {npml}-cell CPML layers")
        ln = grid.length(a)
        nodes = np.full(ln, np.nan)
        cells = np.full(ln, np.nan)
        nodes[1 : n + 2] = grid.lines[a]
        cells[1 : n + 1] = grid.centers(a)
        arrays = {k: np.zeros(ln) for k in ("eb", "ec", "ek", "hb", "hc", "hk")}
        for s in sides:
            if s == 0:
                edge = grid.lines[a][npml]
                dn, dc = edge - nodes, edge - cells
                e_rng, h_rng = (1, npml + 1), (1, npml + 1)
            else:
                edge = grid.lines[a][n - npml]
                dn, dc = nodes - edge, cells - edge
                e_rng, h_rng = (n - npml + 2, n + 2), (n - npml + 1, n + 1)
            thick = abs(edge - (grid.lines[a][0] if s == 0 else grid.lines[a][n]))
            for pre, depth in (("e", dn), ("h", dc)):
                b, c, k = _profile(np.nan_to_num(depth, nan=0.0), thick, spec, dt)
                rng = e_rng if pre == "e" else h_rng
                sl = slice(*rng)
                arrays[pre + "b"][sl] = b[sl]
                arrays[pre + "c"][sl] = c[sl]
                arrays[pre + "k"][sl] = k[sl]
            for kind, terms, rng in (("E", E_TERMS, e_rng), ("H", H_TERMS, h_rng)):
                for c_, a_, b_, sign in terms:
                    if a_ != a:
                        continue
                    box = list(grid.valid(kind, c_))
                    box[a] = rng
                    corrections.append(Correction(kind, c_, a, b_, sign, tuple(box)))  # type: ignore[arg-type]
        coeffs[a] = AxisCoeffs(**arrays)
    return Pml(tuple(corrections), coeffs)


def mu_dt(dt: float) -> float:
    """``dt / mu0`` [m/(H s)]."""
    return dt / MU0
