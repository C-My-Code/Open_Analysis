# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Touchstone writer (RI, full precision): v1.1 for one real reference, v2.0 otherwise.

Port names go in ``! Port[n] = name`` comments. The openEMS adapter has its own copy of this
format; adapters are siblings and do not import each other.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import numpy.typing as npt

ComplexArray = npt.NDArray[np.complex128]
FloatArray = npt.NDArray[np.float64]


def _pair(z: complex) -> str:
    return f"{z.real:.12e} {z.imag:.12e}"


def _order(n: int) -> list[tuple[int, int]]:
    """File order: 2-port files are S11 S21 S12 S22, others row-major."""
    if n == 2:
        return [(0, 0), (1, 0), (0, 1), (1, 1)]
    return [(i, j) for i in range(n) for j in range(n)]


def format_touchstone(
    freqs: FloatArray,
    s: ComplexArray,
    z0: Sequence[float],
    names: Sequence[str],
    comments: Sequence[str] = (),
) -> str:
    n = s.shape[1]
    lines = [f"! {c}" for c in comments]
    lines += [f"! Port[{i + 1}] = {nm}" for i, nm in enumerate(names)]
    uniform = len({float(z) for z in z0}) == 1
    if uniform:
        lines.append(f"# HZ S RI R {float(z0[0]):.12g}")
    else:
        lines += ["[Version] 2.0", "# HZ S RI R 50", f"[Number of Ports] {n}"]
        if n == 2:
            lines.append("[Two-Port Data Order] 21_12")
        lines += [
            f"[Number of Frequencies] {len(freqs)}",
            "[Reference] " + " ".join(f"{float(z):.12g}" for z in z0),
            "[Matrix Format] Full",
            "[Network Data]",
        ]
    order = _order(n)
    for k, f in enumerate(freqs):
        pairs = [_pair(complex(s[k, i, j])) for i, j in order]
        if n <= 2:
            lines.append(f"{float(f):.12e} " + " ".join(pairs))
            continue
        for r in range(n):
            row = pairs[r * n : (r + 1) * n]
            for c in range(0, len(row), 4):
                prefix = f"{float(f):.12e} " if (r == 0 and c == 0) else "  "
                lines.append(prefix + " ".join(row[c : c + 4]))
    if not uniform:
        lines.append("[End]")
    return "\n".join(lines) + "\n"


def write_touchstone(
    path: Path,
    freqs: FloatArray,
    s: ComplexArray,
    z0: Sequence[float],
    names: Sequence[str],
    comments: Sequence[str] = (),
) -> None:
    path.write_text(format_touchstone(freqs, s, z0, names, comments), encoding="utf-8")
