# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Touchstone v1.1 / v2.0 reader and writer for ``NetworkResult`` (interfaces.md section 4.2).

Numbers are written with ``repr`` (shortest round-trip form) in RI format and Hz, so a write/read
cycle is bit-exact. Touchstone cannot express complex or per-frequency reference impedances, so
those are carried in ``! Z0[...]`` comment lines (ignored by other tools; ``R`` and ``[Reference]``
fields hold the real parts). Port names go in ``! Port[n] = name`` comment lines (v1.1 and v2.0)
and in a ``[Port Names]`` keyword (v2.0).
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Literal

import numpy as np
import numpy.typing as npt

from oa_model.arrays import Floats, NDArray
from oa_results.network import NetworkResult, PortMapEntry
from oa_results.quality import QualityCriteria, QualityReport

__all__ = ["TouchstoneError", "from_touchstone", "to_touchstone"]

_UNITS = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}
_PORT_RE = re.compile(r"^\s*Port\[(\d+)\]\s*=\s*(.*?)\s*$")
_Z0_RE = re.compile(r"^\s*Z0\[(\d+)(?:,(\d+))?\]\s*=\s*(.*?)\s*$")


class TouchstoneError(ValueError):
    """Malformed or unsupported Touchstone file."""


def _num(x: float) -> str:
    return repr(float(x))


def to_touchstone(
    net: NetworkResult, path: str | Path, version: Literal["2.0", "1.1"] = "2.0"
) -> Path:
    """Write ``net`` as Touchstone ``version`` (RI format, Hz). Returns ``path``."""
    if version not in ("2.0", "1.1"):
        raise ValueError(f"unsupported Touchstone version {version!r}")
    f = np.asarray(net.freqs.array, dtype=np.float64)
    s = np.asarray(net.s.array, dtype=np.complex128)
    z0 = np.asarray(net.z0.array, dtype=np.complex128)
    nf, n, _ = s.shape
    names = net.port_names if len(net.port_names) == n else tuple(f"P{i + 1}" for i in range(n))
    z0_pf = z0 if z0.ndim == 2 else None
    z0_p = z0[0] if z0_pf is not None else z0
    uniform_real = (
        z0_pf is None and bool(np.all(z0_p.imag == 0.0)) and bool(np.all(z0_p.real == z0_p.real[0]))
    )

    out: list[str] = ["! Open_Analysis Touchstone export"]
    out += [f"! Port[{i + 1}] = {names[i]}" for i in range(n)]
    if not uniform_real:
        if z0_pf is None:
            out += [f"! Z0[{i + 1}] = {_num(v.real)} {_num(v.imag)}" for i, v in enumerate(z0_p)]
        else:
            for k in range(nf):
                vals = " ".join(f"{_num(v.real)} {_num(v.imag)}" for v in z0_pf[k])
                out.append(f"! Z0[{k},{n}] = {vals}")
    r_opt = _num(float(z0_p[0].real))
    if version == "2.0":
        out.append("[Version] 2.0")
    out.append(f"# HZ S RI R {r_opt}")
    if version == "2.0":
        out.append(f"[Number of Ports] {n}")
        if n == 2:
            out.append("[Two-Port Data Order] 21_12")
        out.append(f"[Number of Frequencies] {nf}")
        out.append("[Reference] " + " ".join(_num(v.real) for v in z0_p))
        out.append("[Port Names] " + " ".join(nm.replace(" ", "_") for nm in names))
        out.append("[Matrix Format] Full")
        out.append("[Network Data]")
    for k in range(nf):
        mat = s[k]
        if n == 2:
            order = (mat[0, 0], mat[1, 0], mat[0, 1], mat[1, 1])
            out.append(_num(f[k]) + " " + " ".join(f"{_num(v.real)} {_num(v.imag)}" for v in order))
            continue
        for i in range(n):
            pairs = [f"{_num(v.real)} {_num(v.imag)}" for v in mat[i]]
            for j in range(0, n, 4):
                head = _num(f[k]) + " " if i == 0 and j == 0 else ""
                out.append(head + " ".join(pairs[j : j + 4]))
    if version == "2.0":
        out.append("[End]")
    p = Path(path)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(out) + "\n")
    return p


def _to_complex(a: float, b: float, fmt: str) -> complex:
    if fmt == "RI":
        return complex(a, b)
    mag = 10.0 ** (a / 20.0) if fmt == "DB" else a
    ang = math.radians(b)
    return complex(mag * math.cos(ang), mag * math.sin(ang))


def _placeholder_quality(f: npt.NDArray[np.float64]) -> QualityReport:
    return QualityReport(
        verdict="warn",
        pqm=0.0,
        rqm=0.0,
        cqm=0.0,
        max_singular_value=0.0,
        worst_passivity_freq=None,
        reciprocity_max_err=0.0,
        dc="missing",
        f_min=float(f[0]) if f.size else 0.0,
        f_max=float(f[-1]) if f.size else 0.0,
        n_freqs=int(f.size),
        criteria=QualityCriteria(),
        notes=("unassessed: read from a Touchstone file; run the quality gate before use",),
    )


def from_touchstone(path: str | Path) -> NetworkResult:
    """Read a Touchstone v1.x / v2.0 S-parameter file. The result is *unassessed*."""
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    unit, fmt, r_opt = 1e9, "MA", 50.0  # Touchstone defaults
    n: int | None = None
    m = re.search(r"\.s(\d+)p$", p.name.lower())
    if m:
        n = int(m.group(1))
    two_port_12_21 = False
    reference: list[float] = []
    port_names: dict[int, str] = {}
    z0_ports: dict[int, complex] = {}
    z0_freq: dict[int, list[complex]] = {}  # per-frequency rows
    names_kw: list[str] = []
    data: list[float] = []
    seen_option = False

    for raw in text.splitlines():
        line, _, comment = raw.partition("!")
        if comment:
            pm = _PORT_RE.match(comment)
            if pm:
                port_names[int(pm.group(1)) - 1] = pm.group(2)
            zm = _Z0_RE.match(comment)
            if zm:
                vals = [float(v) for v in zm.group(3).split()]
                cs = [complex(vals[i], vals[i + 1]) for i in range(0, len(vals) - 1, 2)]
                if zm.group(2) is None:
                    z0_ports[int(zm.group(1)) - 1] = cs[0]
                else:
                    z0_freq[int(zm.group(1))] = cs
        line = line.strip()
        if not line:
            continue
        if line.startswith("["):
            key, _, rest = line[1:].partition("]")
            key = key.strip().lower()
            toks = rest.split()
            if key == "number of ports":
                n = int(toks[0])
            elif key == "two-port data order":
                two_port_12_21 = toks[0].lower() == "12_21"
            elif key == "reference":
                reference = [float(t) for t in toks]
            elif key == "port names":
                names_kw = toks
            elif key == "matrix format":
                if toks and toks[0].lower() != "full":
                    raise TouchstoneError(f"{p.name}: only [Matrix Format] Full is supported")
            elif key == "end":
                break
            continue
        if line.startswith("#"):
            if seen_option:
                continue
            seen_option = True
            toks = line[1:].upper().split()
            i = 0
            while i < len(toks):
                t = toks[i]
                if t in _UNITS:
                    unit = _UNITS[t]
                elif t in ("RI", "MA", "DB"):
                    fmt = t
                elif t == "R":
                    r_opt = float(toks[i + 1])
                    i += 1
                elif t in ("Y", "Z", "G", "H"):
                    raise TouchstoneError(f"{p.name}: only S-parameters are supported")
                i += 1
            continue
        data += [float(t) for t in line.split()]

    if n is None:
        raise TouchstoneError(f"{p.name}: number of ports unknown (use a .sNp extension)")
    stride = 1 + 2 * n * n
    if not data or len(data) % stride:
        raise TouchstoneError(f"{p.name}: data length {len(data)} is not a multiple of {stride}")
    arr = np.asarray(data, dtype=np.float64).reshape(-1, stride)
    nf = arr.shape[0]
    f = arr[:, 0] * unit
    s = np.empty((nf, n, n), dtype=np.complex128)
    for k in range(nf):
        cv = [_to_complex(arr[k, 1 + 2 * i], arr[k, 2 + 2 * i], fmt) for i in range(n * n)]
        if n == 2 and not two_port_12_21:
            s[k] = np.array([[cv[0], cv[2]], [cv[1], cv[3]]], dtype=np.complex128)
        else:
            s[k] = np.asarray(cv, dtype=np.complex128).reshape(n, n)

    z0: npt.NDArray[np.complex128]
    if z0_freq:
        z0 = np.asarray([z0_freq[k] for k in range(nf)], dtype=np.complex128)
    elif z0_ports:
        z0 = np.asarray([z0_ports[i] for i in range(n)], dtype=np.complex128)
    elif len(reference) >= n:
        z0 = np.asarray(reference[:n], dtype=np.float64).astype(np.complex128)
    else:
        z0 = np.full(n, r_opt, dtype=np.complex128)

    if port_names:
        names = tuple(port_names.get(i, f"P{i + 1}") for i in range(n))
    elif len(names_kw) == n:
        names = tuple(names_kw)
    else:
        names = tuple(f"P{i + 1}" for i in range(n))
    return NetworkResult(
        freqs=Floats(f),
        s=NDArray(s, dtype="c16"),
        z0=NDArray(z0, dtype="c16"),
        port_names=names,
        port_map=tuple(PortMapEntry(port=nm) for nm in names),
        quality=_placeholder_quality(f),
    )
