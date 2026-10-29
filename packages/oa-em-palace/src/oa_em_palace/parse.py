# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Parsers for Palace output: ``port-S.csv``, AMR ``error-indicators.csv`` and the run footer."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

__all__ = ["RunFooter", "SParams", "parse_footer", "read_csv_table", "read_port_s"]

_S_COL = re.compile(r"^\s*(?P<kind>\|S|arg\(S)\[(?P<i>\d+)\]\[(?P<j>\d+)\]\)?\|?\s*\((?P<unit>dB|deg)\)")


@dataclass(frozen=True)
class SParams:
    """Palace S-parameters: ``s[k, i, j]`` is the wave at port ``i+1`` for excitation ``j+1``."""

    freqs: npt.NDArray[np.float64]  # Hz
    s: npt.NDArray[np.complex128]  # (F, P, P), NaN where the column was not excited
    excited: tuple[int, ...]  # 0-based excitation (column) indices that are present


@dataclass(frozen=True)
class RunFooter:
    """Values the ``oa-palace-run`` wrapper prints at the end of the run."""

    version: str | None
    wall_s: float | None
    peak_mem_kb: int | None


def read_csv_table(path: Path) -> tuple[list[str], npt.NDArray[np.float64]]:
    """Header names (stripped) and the numeric body of a Palace CSV file."""
    text = path.read_text(encoding="utf-8")
    rows = list(csv.reader(io.StringIO(text), skipinitialspace=True))
    rows = [r for r in rows if r and any(c.strip() for c in r)]
    if not rows:
        raise ValueError(f"{path.name} is empty")
    header = [c.strip() for c in rows[0]]
    body = np.array([[float(c) for c in r if c.strip() != ""] for r in rows[1:]], dtype=np.float64)
    if body.ndim != 2 or body.shape[1] != len(header):
        raise ValueError(f"{path.name}: ragged table ({body.shape} vs {len(header)} columns)")
    return header, body


def read_port_s(path: Path) -> SParams:
    """Parse ``port-S.csv`` (columns ``f (GHz)``, ``|S[i][j]| (dB)``, ``arg(S[i][j]) (deg)``)."""
    header, body = read_csv_table(path)
    if not header or not header[0].lower().startswith("f"):
        raise ValueError(f"{path.name}: first column is not a frequency: {header[:1]}")
    unit = re.search(r"\((\w+)\)", header[0])
    scale = {"ghz": 1e9, "mhz": 1e6, "khz": 1e3, "hz": 1.0}[(unit.group(1) if unit else "GHz").lower()]
    mag: dict[tuple[int, int], npt.NDArray[np.float64]] = {}
    ang: dict[tuple[int, int], npt.NDArray[np.float64]] = {}
    for c, name in enumerate(header[1:], start=1):
        m = _S_COL.match(name)
        if m is None:
            continue
        key = (int(m["i"]) - 1, int(m["j"]) - 1)
        (mag if m["kind"] == "|S" else ang)[key] = body[:, c]
    if not mag or mag.keys() != ang.keys():
        raise ValueError(f"{path.name}: no matching |S| / arg(S) column pairs in {header}")
    n = 1 + max(max(i, j) for i, j in mag)
    s = np.full((body.shape[0], n, n), np.nan + 0j, dtype=np.complex128)
    for (i, j), db in mag.items():
        s[:, i, j] = 10.0 ** (db / 20.0) * np.exp(1j * np.deg2rad(ang[(i, j)]))
    return SParams(
        freqs=body[:, 0] * scale, s=s, excited=tuple(sorted({j for _, j in mag}))
    )


def parse_footer(text: str) -> RunFooter:
    """Read the ``OA_PALACE_*`` lines of the wrapper (anywhere in ``text``)."""

    def find(pattern: str) -> str | None:
        found = re.findall(pattern, text, flags=re.MULTILINE)
        return found[-1].strip() if found else None

    ver = find(r"^OA_PALACE_VERSION=(.*)$")
    wall = find(r"^OA_PALACE_WALL_S=([0-9.]+)")
    peak = find(r"^OA_PALACE_PEAK_MEM_KB=([0-9]+)")
    return RunFooter(
        version=ver or None,
        wall_s=float(wall) if wall else None,
        peak_mem_kb=int(peak) if peak else None,
    )
