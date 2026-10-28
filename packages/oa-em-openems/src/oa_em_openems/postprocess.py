# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Raw openEMS port data to S-parameters.

Each excitation run leaves the total voltage ``V`` and current ``I`` of every port at its
measurement plane (``ports.npz``). The S-matrix is assembled from the ``N`` runs with power waves
``a = (V + z I) / (2 sqrt(z))`` and ``b = (V - z I) / (2 sqrt(z))`` for a *real* reference ``z``
per port: ``S = B A^-1`` where column ``k`` of ``A``/``B`` is run ``k``. This renormalizes to any
reference impedance exactly and does not assume that the other ports were matched in the run.
Transmission-line ports are first moved to the requested reference plane with the line's own
impedance and phase constant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

ComplexArray = npt.NDArray[np.complex128]
FloatArray = npt.NDArray[np.float64]


@dataclass
class PortRaw:
    """Port quantities of one run, all shape ``(F,)`` except ``shift``."""

    nr: int
    u: ComplexArray
    i: ComplexArray
    z_line: ComplexArray  # port/line impedance measured by the solver
    beta: ComplexArray
    shift: float  # reference-plane shift towards the DUT [m]


@dataclass
class RunRaw:
    freqs: FloatArray
    excited: int
    ports: dict[int, PortRaw]


def load_run(path: Path) -> RunRaw:
    """Read ``ports.npz`` written by the generated script."""
    with np.load(path) as data:
        freqs = np.array(data["freqs"], dtype=np.float64)
        excited = int(data["excited"][0])
        ports: dict[int, PortRaw] = {}
        for key in data.files:
            m = re.fullmatch(r"u(\d+)", key)
            if not m:
                continue
            nr = int(m.group(1))
            ports[nr] = PortRaw(
                nr=nr,
                u=np.array(data[f"u{nr}"], dtype=np.complex128),
                i=np.array(data[f"i{nr}"], dtype=np.complex128),
                z_line=np.array(data[f"z{nr}"], dtype=np.complex128),
                beta=np.array(data[f"beta{nr}"], dtype=np.complex128),
                shift=float(data[f"shift{nr}"][0]),
            )
    return RunRaw(freqs=freqs, excited=excited, ports=ports)


def shift_reference(
    u: ComplexArray, i: ComplexArray, z_line: ComplexArray, beta: ComplexArray, shift: float
) -> tuple[ComplexArray, ComplexArray]:
    """Move V and I by ``shift`` metres along the line (same formula as openEMS ``CalcPort``)."""
    if shift == 0.0:
        return u, i
    phase = np.real(beta) * shift
    u2 = u * np.cos(-phase) + 1j * i * z_line * np.sin(-phase)
    i2 = i * np.cos(-phase) + 1j * u / z_line * np.sin(-phase)
    return u2, i2


def assemble_s(
    runs: list[RunRaw], port_order: list[int], z0: FloatArray
) -> tuple[ComplexArray, list[str]]:
    """S-matrix ``(F, P, P)`` from the runs, referenced to the real impedances ``z0`` ``(P,)``.

    ``port_order`` lists port numbers in matrix order; every port needs a run (``S_jj`` of an
    unexcited port is unknowable). Returns ``(S, notes)``.
    """
    n = len(port_order)
    freqs = runs[0].freqs
    nf = freqs.size
    a = np.zeros((nf, n, len(runs)), dtype=np.complex128)
    b = np.zeros_like(a)
    for k, run in enumerate(runs):
        for j, nr in enumerate(port_order):
            p = run.ports[nr]
            u, i = shift_reference(p.u, p.i, p.z_line, p.beta, p.shift)
            r = float(z0[j])
            a[:, j, k] = (u + r * i) / (2.0 * np.sqrt(r))
            b[:, j, k] = (u - r * i) / (2.0 * np.sqrt(r))
    excited_idx = [port_order.index(run.excited) for run in runs]
    notes: list[str] = []
    s = np.zeros((nf, n, n), dtype=np.complex128)
    a_ee = a[:, excited_idx, :]
    s_cols = np.einsum("fjk,fkl->fjl", b, np.linalg.inv(a_ee))  # (F, P, K)
    for k, col in enumerate(excited_idx):
        s[:, :, col] = s_cols[:, :, k]
    if any(j not in excited_idx for j in range(n)):
        raise ValueError("S-matrix is undetermined: every port needs its own excitation run")
    return s, notes


def line_impedance_reference(runs: list[RunRaw], port_nr: int) -> float:
    """Median real line impedance of a port over the band (the renormalization target)."""
    zs = np.concatenate([np.real(run.ports[port_nr].z_line) for run in runs])
    return float(np.median(zs))


_STEP_RE = re.compile(
    r"Timestep:\s+(\d+)\s*\|\|.*?Energy:\s*~?([0-9.eE+-]+)\s*\(\s*(-?)\s*([0-9.]+|inf)\s*dB\)"
)


def parse_convergence(stdout: str) -> list[tuple[int, float]]:
    """``(timestep, energy_db)`` pairs from openEMS progress output."""
    rows: list[tuple[int, float]] = []
    for line in stdout.splitlines():
        m = _STEP_RE.search(line)
        if m:
            try:
                rows.append((int(m.group(1)), float(m.group(3) + m.group(4))))
            except ValueError:
                continue
    return rows
