# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal Touchstone writer/reader (S-parameters, RI format, full precision).

``oa_results.io`` (WP-107) owns the general Touchstone I/O. This module exists so the adapter
can write its own deliverable and so its golden tests do not depend on that package. Version 1.1
is written when all ports share one real reference impedance, version 2.0 otherwise. Port names
go in ``! Port[n] = name`` comments, as in TASKS.md WP-107.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import numpy.typing as npt

ComplexArray = npt.NDArray[np.complex128]
FloatArray = npt.NDArray[np.float64]

_PAIRS_PER_LINE = 4


def _pair(z: complex) -> str:
    return f"{z.real:.12e} {z.imag:.12e}"


def _row_order(n: int) -> list[tuple[int, int]]:
    """Matrix entries in file order: 2-port files use S11 S21 S12 S22, others are row-major."""
    if n == 2:
        return [(0, 0), (1, 0), (0, 1), (1, 1)]
    return [(i, j) for i in range(n) for j in range(n)]


def format_touchstone(
    freqs: FloatArray,
    s: ComplexArray,
    z0: Sequence[float],
    port_names: Sequence[str],
    comments: Sequence[str] = (),
) -> str:
    """Return the Touchstone text of ``s`` (shape ``(F, N, N)``) with real references ``z0``."""
    n = s.shape[1]
    lines = [f"! {c}" for c in comments]
    lines.extend(f"! Port[{i + 1}] = {name}" for i, name in enumerate(port_names))
    uniform = len({float(z) for z in z0}) == 1
    order = _row_order(n)
    if uniform:
        lines.append(f"# HZ S RI R {float(z0[0]):.12g}")
    else:
        lines.extend(
            [
                "[Version] 2.0",
                "# HZ S RI R 50",
                f"[Number of Ports] {n}",
            ]
        )
        if n == 2:
            lines.append("[Two-Port Data Order] 21_12")
        lines.extend(
            [
                f"[Number of Frequencies] {len(freqs)}",
                "[Reference] " + " ".join(f"{float(z):.12g}" for z in z0),
                "[Matrix Format] Full",
                "[Network Data]",
            ]
        )
    for k, f in enumerate(freqs):
        pairs = [_pair(complex(s[k, i, j])) for i, j in order]
        if n <= 2:
            lines.append(f"{float(f):.12e} " + " ".join(pairs))
        else:
            per_row = n
            for r in range(n):
                row = pairs[r * per_row : (r + 1) * per_row]
                chunks = [row[c : c + _PAIRS_PER_LINE] for c in range(0, len(row), _PAIRS_PER_LINE)]
                for ci, chunk in enumerate(chunks):
                    prefix = f"{float(f):.12e} " if (r == 0 and ci == 0) else "  "
                    lines.append(prefix + " ".join(chunk))
    if not uniform:
        lines.append("[End]")
    return "\n".join(lines) + "\n"


def write_touchstone(
    path: Path,
    freqs: FloatArray,
    s: ComplexArray,
    z0: Sequence[float],
    port_names: Sequence[str],
    comments: Sequence[str] = (),
) -> None:
    path.write_text(format_touchstone(freqs, s, z0, port_names, comments), encoding="utf-8")


def read_touchstone(path: Path) -> tuple[FloatArray, ComplexArray, list[float]]:
    """Read a file written by :func:`write_touchstone` (``freqs [Hz], S (F, N, N), z0``)."""
    n_ports = int(path.suffix.lower().removeprefix(".s").removesuffix("p"))
    z0 = [50.0] * n_ports
    numbers: list[float] = []
    freq_marks: list[int] = []
    in_data = True
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("!", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[Reference]"):
            z0 = [float(v) for v in line.split()[1:]]
            continue
        if line.startswith("[Network Data]"):
            in_data = True
            continue
        if line.startswith("["):
            continue
        if line.startswith("#"):
            parts = line.split()
            if "R" in parts:
                z0 = [float(parts[parts.index("R") + 1])] * n_ports
            continue
        if not in_data:
            continue
        values = [float(v) for v in line.split()]
        if len(values) % 2 == 1:
            freq_marks.append(len(numbers))
        numbers.extend(values)
    per_freq = 1 + 2 * n_ports * n_ports
    arr = np.array(numbers, dtype=np.float64).reshape(-1, per_freq)
    freqs = arr[:, 0].copy()
    flat = arr[:, 1::2] + 1j * arr[:, 2::2]
    s = np.zeros((freqs.size, n_ports, n_ports), dtype=np.complex128)
    for k, (i, j) in enumerate(_row_order(n_ports)):
        s[:, i, j] = flat[:, k]
    return freqs, s, z0
