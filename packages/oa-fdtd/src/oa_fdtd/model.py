# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Problem description and its compilation into the arrays both engines consume.

Materials follow ADR-0010 section 2: no per-cell coefficients; every edge stores a ``uint16`` index
into a table of averaged materials (eps and sigma averaged over the up-to-four cells around the
edge, area weighted). Index 0 is reserved for PEC. Kernels rebuild ``ca, cb`` from the table.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from oa_fdtd.grid import EPS0, Grid
from oa_fdtd.pml import CpmlSpec, Pml, build_pml

FloatArray = npt.NDArray[np.float64]
WALL_KINDS = ("pec", "pmc", "cpml")
MAX_TABLE = 65535


class ModelError(ValueError):
    """Bad problem description. ``code`` is a stable ``FDTD_*`` identifier."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.hint = code, message, hint


@dataclass(frozen=True)
class Waveform:
    """Excitation ``s(t)``. ``kind`` ``"gauss_mod"``: modulated Gaussian without DC (default);
    ``"gauss"``: Gaussian with DC content.
    """

    kind: str = "gauss_mod"
    f0: float = 0.0  # [Hz] carrier (gauss_mod)
    bandwidth: float = 0.0  # [Hz] -20 dB full width (gauss_mod) or highest frequency (gauss)
    delay_tau: float = 5.0  # [-] start delay in units of tau

    @property
    def tau(self) -> float:
        """Gaussian width [s]."""
        if self.bandwidth <= 0:
            raise ModelError("FDTD_BAD_WAVEFORM", "bandwidth must be positive")
        if self.kind == "gauss_mod":
            return 2.0 * math.sqrt(math.log(10.0)) / (math.pi * self.bandwidth)
        return math.sqrt(math.log(10.0)) / (math.pi * self.bandwidth)

    def __call__(self, t: FloatArray) -> FloatArray:
        tau = self.tau
        t0 = self.delay_tau * tau
        env = np.exp(-(((t - t0) / tau) ** 2))
        if self.kind == "gauss":
            out: FloatArray = env
            return out
        out = env * np.sin(2.0 * math.pi * self.f0 * (t - t0))
        return out


@dataclass(frozen=True)
class Region:
    """Box of material (cells whose centre lies inside) or PEC (edges inside the closed box)."""

    lo: tuple[float, float, float]  # [m]
    hi: tuple[float, float, float]  # [m]
    eps_r: float = 1.0  # [-]
    sigma: float = 0.0  # [S/m]
    pec: bool = False


@dataclass(frozen=True)
class LumpedPort:
    """Resistive voltage source over all edges of one component inside the box (Piket-May 1994)."""

    name: str
    axis: int  # field direction: 0, 1, 2
    lo: tuple[float, float, float]  # [m]
    hi: tuple[float, float, float]  # [m]
    r_ohm: float = 50.0  # [ohm]
    sign: float = 1.0  # +1: positive voltage drives +axis


@dataclass(frozen=True)
class SoftSource:
    """Additive source ``E += amplitude * s(t)`` on all E edges of a component in the box."""

    name: str
    axis: int
    lo: tuple[float, float, float]  # [m]
    hi: tuple[float, float, float]  # [m]
    amplitude: float = 1.0  # [V/m] per unit waveform


@dataclass(frozen=True)
class Probe:
    """Weighted sum of one field component over a box, recorded per step and DFT'd."""

    name: str
    kind: str  # "E" or "H"
    axis: int
    lo: tuple[float, float, float]  # [m]
    hi: tuple[float, float, float]  # [m]
    weight: str = "sum"  # "sum": sum f; "line": sum f * edge length along the component; "mean"


@dataclass
class Model:
    """A complete engine-level problem."""

    grid: Grid
    waveform: Waveform
    frequencies: FloatArray  # [Hz]
    regions: list[Region] = field(default_factory=list)
    walls: tuple[str, str, str, str, str, str] = ("pec",) * 6  # xlo, xhi, ylo, yhi, zlo, zhi
    cpml: CpmlSpec = field(default_factory=CpmlSpec)
    ports: list[LumpedPort] = field(default_factory=list)
    sources: list[SoftSource] = field(default_factory=list)
    probes: list[Probe] = field(default_factory=list)
    drive: FloatArray | None = (
        None  # (B, channels) amplitude per batch member; channels: ports, sources
    )
    courant: float = 0.95  # [-]

    @property
    def channels(self) -> int:
        return len(self.ports) + len(self.sources)

    def drive_matrix(self) -> FloatArray:
        if self.drive is not None:
            d = np.atleast_2d(np.asarray(self.drive, dtype=np.float64))
            if d.shape[1] != self.channels:
                raise ModelError("FDTD_BAD_DRIVE", "drive needs one column per port and source")
            return d
        if self.ports:
            return np.concatenate(
                [np.eye(len(self.ports)), np.ones((len(self.ports), len(self.sources)))], axis=1
            )
        return np.ones((1, self.channels))


@dataclass
class Compiled:
    """Everything the engines need, as plain arrays."""

    grid: Grid
    dt: float
    idx: list[npt.NDArray[np.uint16]]  # per E component, shape grid.shape
    eps_tab: FloatArray  # [-] relative permittivity per table entry (0 for PEC)
    sig_tab: FloatArray  # [S/m]
    ca_tab: FloatArray
    cb_tab: FloatArray  # [m/(F s)... dt / eps]
    walls: tuple[str, ...]
    pml: Pml
    channels: list[dict[str, Any]]  # comp, flat, amps (per unit waveform), waveform
    probes: list[dict[str, Any]]  # name, kind, comp, flat, weights
    ports: list[dict[str, Any]]
    drive: FloatArray
    frequencies: FloatArray

    @property
    def batch(self) -> int:
        return int(self.drive.shape[0])


def _cell_arrays(model: Model) -> tuple[FloatArray, FloatArray]:
    g = model.grid
    nx, ny, nz = g.n
    eps = np.ones((nz + 2, ny + 2, nx + 2))
    sig = np.zeros_like(eps)
    cx, cy, cz = (g.centers(a) for a in range(3))
    for r in model.regions:
        if r.pec:
            continue
        mx = (cx >= r.lo[0]) & (cx <= r.hi[0])
        my = (cy >= r.lo[1]) & (cy <= r.hi[1])
        mz = (cz >= r.lo[2]) & (cz <= r.hi[2])
        sel = np.ix_(np.flatnonzero(mz) + 1, np.flatnonzero(my) + 1, np.flatnonzero(mx) + 1)
        eps[sel] = r.eps_r
        sig[sel] = r.sigma
    return eps, sig


def _edge_average(
    g: Grid, comp: int, eps: FloatArray, sig: FloatArray
) -> tuple[FloatArray, FloatArray]:
    """Area-weighted average of the four cells around every edge, over ``[1, n + 1]`` per axis."""
    nx, ny, nz = g.n
    w = [g.cell_weights(a)[: g.n[a] + 2] for a in range(3)]
    others = [a for a in range(3) if a != comp]
    p, q = others  # transverse axes, p < q
    num_e: FloatArray = np.zeros((nz + 1, ny + 1, nx + 1))
    num_s = np.zeros_like(num_e)
    den = np.zeros_like(num_e)
    for da in (0, 1):
        for db in (0, 1):
            off = [0, 0, 0]
            off[p], off[q] = da, db
            # storage axis order is (z, y, x): index by axis number reversed
            sl = tuple(slice(1 - off[ax], g.n[ax] + 2 - off[ax]) for ax in (2, 1, 0))
            wp = w[p][1 - da : g.n[p] + 2 - da]
            wq = w[q][1 - db : g.n[q] + 2 - db]
            wgt = np.ones((nz + 1, ny + 1, nx + 1))
            wgt = wgt * _bshape(wp, p) * _bshape(wq, q)
            num_e += wgt * eps[sl]
            num_s += wgt * sig[sl]
            den += wgt
    return num_e / den, num_s / den


def _bshape(v: FloatArray, axis: int) -> FloatArray:
    """Reshape a 1-D array for broadcasting along ``axis`` of a ``(z, y, x)`` array."""
    shape = [1, 1, 1]
    shape[2 - axis] = v.size
    return v.reshape(shape)


def compile_model(model: Model, dtype: str = "float32") -> Compiled:
    """Build indices, tables, PML, channels and probes. Raises :class:`ModelError`."""
    g = model.grid
    nx, ny, nz = g.n
    if len(model.walls) != 6 or any(w not in WALL_KINDS for w in model.walls):
        raise ModelError("FDTD_BAD_WALLS", f"walls must be six of {WALL_KINDS}")
    eps_min = min([1.0] + [r.eps_r for r in model.regions if not r.pec])
    dt = g.dt(model.courant, 299792458.0 / math.sqrt(eps_min))
    fast = not model.regions and not model.ports
    eps_c, sig_c = _cell_arrays(model)

    # per-edge (eps, sigma), PEC mask
    edge_eps: list[FloatArray] = []
    edge_sig: list[FloatArray] = []
    pec: list[npt.NDArray[np.bool_]] = []
    for c in range(3):
        if fast:
            edge_eps.append(np.ones((nz + 1, ny + 1, nx + 1)))
            edge_sig.append(np.zeros((nz + 1, ny + 1, nx + 1)))
        else:
            e, s = _edge_average(g, c, eps_c, sig_c)
            edge_eps.append(e)
            edge_sig.append(s)
        valid = np.zeros((nz + 1, ny + 1, nx + 1), dtype=bool)
        (x0, x1), (y0, y1), (z0, z1) = g.valid("E", c)
        valid[z0 - 1 : z1 - 1, y0 - 1 : y1 - 1, x0 - 1 : x1 - 1] = True
        pec.append(~valid)
    # PEC walls (tangential E), PEC and CPML outer planes
    for a in range(3):
        for side in (0, 1):
            if model.walls[2 * a + side] == "pmc":
                continue
            plane = 0 if side == 0 else g.n[a]  # node index in the (n+1)-sized arrays
            for c in range(3):
                if c == a:
                    continue
                sl: list[Any] = [slice(None)] * 3
                sl[2 - a] = plane
                pec[c][tuple(sl)] = True
    for r in model.regions:
        if not r.pec:
            continue
        for c in range(3):
            sl2: list[Any] = [slice(None)] * 3
            for a in range(3):
                node_type = a != c
                co = g.coords(a, node_type)
                tol = 1e-6 * float(np.min(g.d(a)))
                ids = np.flatnonzero((co >= r.lo[a] - tol) & (co <= r.hi[a] + tol))
                sl2[2 - a] = ids if ids.size else np.array([], dtype=np.intp)
            pec[c][np.ix_(sl2[0], sl2[1], sl2[2])] = True

    # lumped ports: loading conductivity and sources
    channels: list[dict[str, Any]] = []
    ports_info: list[dict[str, Any]] = []
    probes: list[dict[str, Any]] = []
    for port in model.ports:
        if port.r_ohm <= 0:
            raise ModelError("FDTD_BAD_PORT", f"port {port.name}: r_ohm must be positive")
        flat, counts = g.points("E", port.axis, port.lo, port.hi)
        k, j, i = np.unravel_index(flat, g.shape)
        ijk = (i, j, k)
        a = port.axis
        trans = [t for t in range(3) if t != a]
        length = g.d(a)[ijk[a] - 1]
        # one column per unique transverse position; area = product of node weights
        area = g.node_weights(trans[0])[ijk[trans[0]]] * g.node_weights(trans[1])[ijk[trans[1]]]
        n_ser = counts[a]
        total_len = float(length.sum()) / (flat.size / n_ser)  # series length of one column
        total_area = float(area.sum()) / n_ser  # sum over columns
        sig_eq = total_len / (port.r_ohm * total_area)
        edge_sig[a][k - 1, j - 1, i - 1] += sig_eq
        ports_info.append(
            {
                "name": port.name,
                "axis": a,
                "r_ohm": port.r_ohm,
                "sign": port.sign,
                "flat": flat,
                "channel": len(channels),
                "sig_eq": sig_eq,
                "total_len": total_len,
                "total_area": total_area,
            }
        )
        channels.append({"comp": a, "flat": flat, "port": len(ports_info) - 1, "sign": port.sign})
        probes.append(
            {
                "name": f"V:{port.name}",
                "kind": "E",
                "comp": a,
                "flat": flat,
                "weights": length * area / total_area,
            }
        )
    for src in model.sources:
        flat, _ = g.points("E", src.axis, src.lo, src.hi)
        channels.append({"comp": src.axis, "flat": flat, "amp": src.amplitude})
    for pr in model.probes:
        flat, _ = g.points(pr.kind, pr.axis, pr.lo, pr.hi)
        k, j, i = np.unravel_index(flat, g.shape)
        ijk = (i, j, k)
        if pr.weight == "sum":
            w = np.ones(flat.size)
        elif pr.weight == "mean":
            w = np.full(flat.size, 1.0 / flat.size)
        elif pr.weight == "line":
            ax = pr.axis
            w = (g.d(ax)[ijk[ax] - 1] if pr.kind == "E" else g.node_weights(ax)[ijk[ax]]).astype(
                np.float64
            )
        else:
            raise ModelError("FDTD_BAD_PROBE", f"probe {pr.name}: weight {pr.weight!r}")
        probes.append(
            {"name": pr.name, "kind": pr.kind, "comp": pr.axis, "flat": flat, "weights": w}
        )

    # table of averaged materials
    idx: list[npt.NDArray[np.uint16]] = []
    rows_list = []
    for c in range(3):
        keep = ~pec[c]
        rows_list.append(np.stack([edge_eps[c][keep], edge_sig[c][keep]], axis=1))
    rows = np.concatenate(rows_list)
    rows = np.round(rows, 12)
    if rows.size:
        uniq, inv = np.unique(rows, axis=0, return_inverse=True)
        inv = inv.ravel()
    else:
        uniq, inv = np.zeros((0, 2)), np.zeros(0, dtype=np.intp)
    if uniq.shape[0] + 1 > MAX_TABLE:
        raise ModelError(
            "FDTD_TOO_MANY_MATERIALS",
            f"{uniq.shape[0]} averaged materials exceed the uint16 table",
            "Use fewer distinct materials or a more uniform grid.",
        )
    eps_tab = np.concatenate([[0.0], uniq[:, 0]])
    sig_tab = np.concatenate([[0.0], uniq[:, 1]])
    pos = 0
    for c in range(3):
        keep = ~pec[c]
        full = np.zeros((nz + 1, ny + 1, nx + 1), dtype=np.uint16)
        full[keep] = (inv[pos : pos + int(keep.sum())] + 1).astype(np.uint16)
        pos += int(keep.sum())
        arr = np.zeros(g.shape, dtype=np.uint16)
        arr[1 : nz + 2, 1 : ny + 2, 1 : nx + 2] = full
        idx.append(arr)
    ep = eps_tab * EPS0
    loss = sig_tab * dt / (2.0 * np.where(ep > 0, ep, 1.0))
    ca_tab = np.where(eps_tab > 0, (1.0 - loss) / (1.0 + loss), 0.0)
    cb_tab = np.where(eps_tab > 0, dt / np.where(ep > 0, ep, 1.0) / (1.0 + loss), 0.0)

    # injection amplitudes (per unit waveform) now that cb is known
    for ch in channels:
        flat = ch["flat"]
        mat = idx[ch["comp"]].ravel()[flat]
        if "port" in ch:
            p = ports_info[ch["port"]]
            ch["amps"] = ch["sign"] * cb_tab[mat] * p["sig_eq"] / p["total_len"]
        else:
            ch["amps"] = ch["amp"] * np.ones(flat.size)
        ch["waveform"] = model.waveform
    pml = build_pml(g, model.walls, model.cpml, dt)
    return Compiled(
        grid=g,
        dt=dt,
        idx=idx,
        eps_tab=eps_tab,
        sig_tab=sig_tab,
        ca_tab=ca_tab,
        cb_tab=cb_tab,
        walls=model.walls,
        pml=pml,
        channels=channels,
        probes=probes,
        ports=ports_info,
        drive=model.drive_matrix(),
        frequencies=np.asarray(model.frequencies, dtype=np.float64),
    )
