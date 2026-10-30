# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Parallel-plate lines with dispersive dielectric and surface-impedance copper, and their
analytic S-parameters (ABCD cascade between resistive ports)."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from oa_fdtd.grid import C0, EPS0, ETA0, MU0, Grid
from oa_fdtd.model import Conductor, LumpedPort, Model, Region, Waveform

ComplexArray = npt.NDArray[np.complex128]
W, H = 0.008, 0.001  # plate width and gap [m]


def abcd_s(
    gamma: ComplexArray, zc: ComplexArray, length: float, r1: float, r2: float
) -> tuple[ComplexArray, ComplexArray]:
    """``(S11, S21)`` of a line section between port resistances ``r1`` and ``r2``."""
    a = np.cosh(gamma * length)
    b = zc * np.sinh(gamma * length)
    c = np.sinh(gamma * length) / zc
    d = a
    den = a * r2 + b + c * r1 * r2 + d * r1
    s11 = (a * r2 + b - c * r1 * r2 - d * r1) / den
    s21 = 2.0 * np.sqrt(r1 * r2) / den
    return s11, s21


def plate_line(
    length: float,
    dy: float,
    freqs: npt.NDArray[np.float64],
    f_max: float,
    *,
    poles: tuple[tuple[float, float], ...] = (),
    eps_inf: float = 1.0,
    sigma_cu: float | None = None,
    t_cu: float = 0.25e-3,
    r_port: float | None = None,
) -> Model:
    """TEM line between two plates, PMC side walls, ports spanning the gap at both ends.

    The plates are boxes of thickness ``t_cu`` (one cell): SIBC copper with ``sigma_cu``, else PEC.
    """
    nx, nz_gap = 4, 4
    dz = H / nz_gap
    ny = round(length / dy)
    zl = [0.0, t_cu, *[t_cu + (i + 1) * dz for i in range(nz_gap)], 2 * t_cu + H]
    g = Grid(np.linspace(0, W, nx + 1), np.arange(ny + 1) * dy, np.array(zl))
    z0 = (ETA0 * H / W / np.sqrt(eps_inf)) if r_port is None else r_port
    ports = [
        LumpedPort("p1", 2, (0, 0, t_cu), (W, 0, t_cu + H), z0),
        LumpedPort("p2", 2, (0, ny * dy, t_cu), (W, ny * dy, t_cu + H), z0),
    ]
    regions = [Region((0, 0, t_cu), (W, length, t_cu + H), eps_inf, poles=poles)] if poles else []
    if not poles and eps_inf != 1.0:
        regions = [Region((0, 0, t_cu), (W, length, t_cu + H), eps_inf)]
    big = 1.0
    kind = "pec" if sigma_cu is None else "thick_sibc"
    sig = 5.8e7 if sigma_cu is None else sigma_cu
    conductors = [
        Conductor((-big, -big, -big), (W + big, length + big, t_cu), sig, t_cu, 0.0, kind),
        Conductor(
            (-big, -big, t_cu + H), (W + big, length + big, 3 * t_cu + H), sig, t_cu, 0.0, kind
        ),
    ]
    return Model(
        g,
        Waveform("gauss_mod", f_max / 2.0, f_max),
        freqs,
        regions=regions,
        conductors=conductors,
        walls=("pmc", "pmc", "pmc", "pmc", "pec", "pec"),
        ports=ports,
    )


def dielectric_line_s(
    freqs: npt.NDArray[np.float64], eps: ComplexArray, length: float, r_port: float
) -> tuple[ComplexArray, ComplexArray]:
    """Analytic S of the parallel-plate line filled with ``eps(f)`` (complex relative)."""
    w = 2 * np.pi * freqs
    root = np.sqrt(eps)
    gamma = 1j * w * root / C0
    zc = ETA0 * H / W / root
    return abcd_s(gamma, zc, length, r_port, r_port)


def copper_line_s(
    freqs: npt.NDArray[np.float64], zs: ComplexArray, length: float, r_port: float
) -> tuple[ComplexArray, ComplexArray]:
    """Analytic S of the vacuum line whose two plates have surface impedance ``zs`` [ohm/sq]."""
    w = 2 * np.pi * freqs
    z_series = 1j * w * MU0 * H / W + 2.0 * zs / W
    y_shunt = 1j * w * EPS0 * W / H
    gamma = np.sqrt(z_series * y_shunt)
    zc = np.sqrt(z_series / y_shunt)
    return abcd_s(gamma, zc, length, r_port, r_port)
