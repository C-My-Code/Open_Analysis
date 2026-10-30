# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Problem description and its compilation into the arrays both engines consume.

Materials follow ADR-0010 section 2: no per-cell coefficients; every edge stores a ``uint16`` index
into a table of averaged materials (eps and sigma averaged over the up-to-four cells around the
edge, area weighted). Index 0 is reserved for PEC. Kernels rebuild ``ca, cb`` from the table.
"""

from __future__ import annotations

import math
from collections.abc import Callable
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
    eps_r: float = 1.0  # [-] (high-frequency value eps_inf when ``poles`` is given)
    sigma: float = 0.0  # [S/m]
    pec: bool = False
    poles: tuple[tuple[float, float], ...] = ()  # Debye poles (delta_eps [-], tau [s])


@dataclass(frozen=True)
class Conductor:
    """Copper box with a surface-impedance boundary on its faces (ADR-0010 section 3).

    The edges strictly inside the box are PEC. Tangential E edges on a face are set from the
    adjacent outside H through the fitted ``Zs(w)`` of a finite slab (shorted far side) times the
    roughness factor, by recursive convolution (:mod:`oa_fdtd.subcell.sibc`).
    """

    lo: tuple[float, float, float]  # [m]
    hi: tuple[float, float, float]  # [m]
    sigma: float = 5.8e7  # [S/m]
    thickness: float | None = None  # [m] slab thickness for Zs; None = smallest box extent
    roughness_rms: float = 0.0  # [m] RMS roughness (Hammerstad-Jensen factor); 0 = smooth
    k_func: Callable[[FloatArray], FloatArray] | None = None  # K(f) [-] overrides roughness_rms
    model: str = "thick_sibc"  # "thick_sibc" | "pec"


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
class ModePort:
    """Quasi-TEM mode port on a plane (ADR-0010 section 6; :mod:`oa_fdtd.ports`).

    The plane ``axis = position`` is cut by the in-plane rectangle ``lo .. hi`` (the two remaining
    axes in ascending order). The mode comes from a 2-D electrostatic solve with ``signals`` as
    the signal conductors (rectangles in the same in-plane axes); every other conductor, PEC
    region and PEC wall is ground. Several ports with the same rectangle and signals but different
    ``mode`` give the N modes of N coupled conductors. The reference impedance is the modal one.
    """

    name: str
    axis: int  # plane normal: 0, 1, 2
    position: float  # [m]
    lo: tuple[float, float]  # [m] rectangle corner (smaller coordinates)
    hi: tuple[float, float]  # [m]
    signals: tuple[tuple[tuple[float, float], tuple[float, float]], ...]  # (lo, hi) per conductor
    mode: int = 0
    direction: int = 1  # +1: the structure lies toward +axis from the plane
    sign: float = 1.0


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
    conductors: list[Conductor] = field(default_factory=list)
    walls: tuple[str, str, str, str, str, str] = ("pec",) * 6  # xlo, xhi, ylo, yhi, zlo, zhi
    cpml: CpmlSpec = field(default_factory=CpmlSpec)
    ports: list[LumpedPort | ModePort] = field(default_factory=list)
    sources: list[SoftSource] = field(default_factory=list)
    mode_probes: list[ModePort] = field(default_factory=list)  # passive modal V probes
    probes: list[Probe] = field(default_factory=list)
    drive: FloatArray | None = (
        None  # (B, channels) amplitude per batch member; channels: ports, sources
    )
    courant: float = 0.95  # [-]
    excite: tuple[str, ...] | None = None  # ports driven (one batch member each); None = all
    f_band: tuple[float, float] | None = (
        None  # excitation band [Hz]; None = (f_min, f_max) of the list
    )

    @property
    def band(self) -> tuple[float, float]:
        """Band [Hz] that sets every static quantity (mode eps, SIBC fit): never the sample list
        when ``f_band`` is given, so S cannot depend on how many frequencies are requested."""
        if self.f_band is not None:
            return self.f_band
        f = np.asarray(self.frequencies, dtype=np.float64)
        return (float(f.min()), float(f.max()))

    @property
    def channels(self) -> int:
        return len(self.ports) + len(self.sources)

    def excited_indices(self) -> list[int]:
        """Indices of the driven ports, in batch order."""
        if self.excite is None:
            return list(range(len(self.ports)))
        names = [p.name for p in self.ports]
        missing = [q for q in self.excite if q not in names]
        if missing:
            raise ModelError("FDTD_BAD_DRIVE", f"excite names unknown ports {missing}")
        return [names.index(q) for q in self.excite]

    def drive_matrix(self) -> FloatArray:
        if self.drive is not None:
            d = np.atleast_2d(np.asarray(self.drive, dtype=np.float64))
            if d.shape[1] != self.channels:
                raise ModelError("FDTD_BAD_DRIVE", "drive needs one column per port and source")
            return d
        if self.ports:
            rows = self.excited_indices()
            eye = np.eye(len(self.ports))[rows]
            return np.concatenate([eye, np.ones((len(rows), len(self.sources)))], axis=1)
        return np.ones((1, self.channels))


@dataclass
class AdeData:
    """Debye ADE edge lists (one entry per E component that has dispersive edges)."""

    kp: FloatArray  # (P,) (1 - dt / 2 tau) / (1 + dt / 2 tau)
    tau: FloatArray  # (P,) [s]
    comp: list[int]
    flat: list[npt.NDArray[np.int64]]  # storage index of each dispersive edge
    beta: list[FloatArray]  # (P, ne) eps0 d_p / (tau_p + dt / 2) [F/s]
    cbe: list[FloatArray]  # (ne,) cb of the edge's averaged material


@dataclass
class SibcData:
    """Surface-impedance edge list with its recursive-convolution tables."""

    comp: npt.NDArray[np.int32]  # E component of each SIBC edge
    flat: npt.NDArray[np.int64]
    hcomp: npt.NDArray[np.int32]  # (ne, 2) H component of the outside samples
    hflat: npt.NDArray[np.int64]  # (ne, 2)
    hsign: FloatArray  # (ne, 2) sign / count; 0 = unused
    mat: npt.NDArray[np.int32]  # (ne,) row in the tables below
    r0: FloatArray  # (M,) [ohm]
    rp: FloatArray  # (M, P) [ohm]
    cp: FloatArray  # (P,) (2 tau / dt) / (1 + 2 tau / dt)
    gp: FloatArray  # (P,) (2 tau / dt - 1) / (2 tau / dt + 1)


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
    ade: AdeData | None = None
    sibc: SibcData | None = None
    probe_sums: dict[str, list[str]] = field(default_factory=dict)  # name -> part probe names

    @property
    def batch(self) -> int:
        return int(self.drive.shape[0])


def _pole_taus(model: Model) -> FloatArray:
    """Sorted union of the relaxation times of all dispersive regions [s]."""
    taus = {round(t, 18) for r in model.regions if not r.pec for _, t in r.poles}
    return np.array(sorted(taus), dtype=np.float64)


def _cell_arrays(model: Model, taus: FloatArray) -> tuple[FloatArray, FloatArray, list[FloatArray]]:
    g = model.grid
    nx, ny, nz = g.n
    eps = np.ones((nz + 2, ny + 2, nx + 2))
    sig = np.zeros_like(eps)
    dels = [np.zeros_like(eps) for _ in taus]
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
        by_tau = {round(t, 18): d for d, t in r.poles}
        for pi, t in enumerate(taus):
            dels[pi][sel] = by_tau.get(round(float(t), 18), 0.0)
    return eps, sig, dels


def _edge_average(g: Grid, comp: int, arrays: list[FloatArray]) -> list[FloatArray]:
    """Area-weighted average of the four cells around every edge, over ``[1, n + 1]`` per axis."""
    nx, ny, nz = g.n
    w = [g.cell_weights(a)[: g.n[a] + 2] for a in range(3)]
    others = [a for a in range(3) if a != comp]
    p, q = others  # transverse axes, p < q
    num = [np.zeros((nz + 1, ny + 1, nx + 1)) for _ in arrays]
    den = np.zeros((nz + 1, ny + 1, nx + 1))
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
            for acc, a in zip(num, arrays, strict=True):
                acc += wgt * a[sl]
            den += wgt
    return [n / den for n in num]


def _bshape(v: FloatArray, axis: int) -> FloatArray:
    """Reshape a 1-D array for broadcasting along ``axis`` of a ``(z, y, x)`` array."""
    shape = [1, 1, 1]
    shape[2 - axis] = v.size
    return v.reshape(shape)


def _compile_ade(
    g: Grid,
    dt: float,
    taus: FloatArray,
    edge_del: list[list[FloatArray]],
    pec: list[npt.NDArray[np.bool_]],
    idx: list[npt.NDArray[np.uint16]],
    cb_tab: FloatArray,
) -> AdeData | None:
    """Edge lists of the dispersive edges (any pole strength > 0, not PEC)."""
    if taus.size == 0:
        return None
    tau = np.asarray(taus, dtype=np.float64)
    comp: list[int] = []
    flats: list[npt.NDArray[np.int64]] = []
    betas: list[FloatArray] = []
    cbes: list[FloatArray] = []
    for c in range(3):
        strength = np.zeros_like(edge_del[c][0])
        for d_p in edge_del[c]:
            strength += d_p
        mask = (strength > 1e-12) & ~pec[c]
        k, j, i = np.nonzero(mask)
        if k.size == 0:
            continue
        flat = np.ravel_multi_index((k + 1, j + 1, i + 1), g.shape).astype(np.int64)
        beta = np.stack(
            [EPS0 * edge_del[c][p][mask] / (tau[p] + 0.5 * dt) for p in range(tau.size)]
        )
        comp.append(c)
        flats.append(flat)
        betas.append(beta)
        cbes.append(cb_tab[idx[c][k + 1, j + 1, i + 1]])
    if not comp:
        return None
    kp = (1.0 - 0.5 * dt / tau) / (1.0 + 0.5 * dt / tau)
    return AdeData(kp=kp, tau=tau, comp=comp, flat=flats, beta=betas, cbe=cbes)


def _compile_conductors(
    model: Model, g: Grid, pec: list[npt.NDArray[np.bool_]], dt: float
) -> SibcData | None:
    """Mark copper interiors PEC and build the surface-impedance edge list."""
    if not model.conductors:
        return None
    from oa_fdtd.subcell.edges import classify_box, outside_samples
    from oa_fdtd.subcell.sibc import SibcFit, fit_surface_impedance

    freqs = np.asarray(model.frequencies, dtype=np.float64)
    wf = model.waveform
    top = wf.f0 + 0.5 * wf.bandwidth if wf.kind == "gauss_mod" else wf.bandwidth
    f_hi = float(max(model.band[1], top))
    f_lo = (
        max(f_hi * 1e-3, 1e5)
        if model.f_band is not None
        else max(float(np.min(freqs)) * 0.5, f_hi * 1e-3, 1e5)
    )
    fits: dict[tuple[float, float, float, int], int] = {}
    mats: list[SibcFit] = []
    pre_pec = [p.copy() for p in pec]
    boxes = []
    for cd in model.conductors:
        edges = classify_box(g, cd.lo, cd.hi)
        for c in range(3):
            pec[c][edges.interior[c]] = True
        if cd.model == "pec":
            for c in range(3):
                pec[c][edges.surface[c]] = True
            continue
        if cd.model != "thick_sibc":
            raise ModelError("FDTD_BAD_CONDUCTOR", f"conductor model {cd.model!r}")
        thick = (
            cd.thickness
            if cd.thickness is not None
            else min(h - lo for lo, h in zip(cd.lo, cd.hi, strict=True))
        )
        if thick <= 0:
            raise ModelError("FDTD_BAD_CONDUCTOR", "conductor box has zero thickness")
        key = (cd.sigma, float(thick), cd.roughness_rms, id(cd.k_func))
        if key not in fits:
            fits[key] = len(mats)
            mats.append(
                fit_surface_impedance(
                    cd.sigma, thick, f_lo, f_hi, rms=cd.roughness_rms, roughness=cd.k_func
                )
            )
        boxes.append((edges, fits[key]))
    parts: list[tuple[Any, ...]] = []
    for edges, m in boxes:
        for c in range(3):
            k, j, i = edges.surface[c]
            if k.size == 0:
                continue
            keep = ~pec[c][k, j, i]  # interiors of any box, walls and PEC regions win
            if not keep.any():
                continue
            k, j, i = k[keep], j[keep], i[keep]
            fa, fb = edges.face_a[c][keep], edges.face_b[c][keep]
            p, hc, hf, hs = outside_samples(g, c, k, j, i, fa, fb)
            parts.append((np.full(p.size, c), p, hc, hf, hs, np.full(p.size, m), k, j, i))
    if not parts:
        return None
    cat = [np.concatenate([q[n] for q in parts]) for n in range(9)]
    c_all, p_all = cat[0], cat[1]
    key_all = c_all.astype(np.int64) * (1 << 44) + p_all
    # later boxes win a shared edge: keep the last occurrence of every key
    _, rev = np.unique(key_all[::-1], return_index=True)
    sel = np.sort(len(key_all) - 1 - rev)
    cat = [v[sel] for v in cat]
    order = np.argsort(cat[0].astype(np.int64) * (1 << 44) + cat[1], kind="stable")
    cat = [v[order] for v in cat]
    for c in range(3):
        m = cat[0] == c
        pec[c][cat[6][m], cat[7][m], cat[8][m]] = True  # zeroed by the bulk update, set by SIBC
    n_p = max(f.rp.size for f in mats)
    tau = mats[0].tau
    for f in mats[1:]:
        if f.tau.shape != tau.shape or not np.allclose(f.tau, tau):
            raise ModelError("FDTD_BAD_CONDUCTOR", "conductors need one common pole set")
    rp = np.zeros((len(mats), n_p))
    for mi, f in enumerate(mats):
        rp[mi, : f.rp.size] = f.rp
    cp, gp = mats[0].recursion(dt)
    del pre_pec
    return SibcData(
        comp=cat[0].astype(np.int32),
        flat=cat[1].astype(np.int64),
        hcomp=cat[2].astype(np.int32),
        hflat=cat[3].astype(np.int64),
        hsign=cat[4].astype(np.float64),
        mat=cat[5].astype(np.int32),
        r0=np.array([f.r0 for f in mats]),
        rp=rp,
        cp=cp,
        gp=gp,
    )


def _mode_probe_parts(
    g: Grid,
    port: ModePort,
    mp: dict[str, Any],
    probes: list[dict[str, Any]],
    edge_sig: list[FloatArray],
    *,
    load: bool,
) -> list[str]:
    """Add the per-component modal projection probes (and the loading when ``load``)."""
    parts: list[str] = []
    lo_ix = 0
    for comp_seg, nseg in zip(sorted({int(c) for c in mp["comp"]}), mp["split"], strict=True):
        sl_seg = slice(lo_ix, lo_ix + nseg)
        lo_ix += nseg
        fl = mp["flat"][sl_seg]
        if load:
            kk, jj, ii = np.unravel_index(fl, g.shape)
            edge_sig[comp_seg][kk - 1, jj - 1, ii - 1] += mp["sigma"][sl_seg]
        parts.append(f"V:{port.name}:{comp_seg}")
        probes.append(
            {
                "name": parts[-1],
                "kind": "E",
                "comp": comp_seg,
                "flat": fl,
                "weights": mp["weights"][sl_seg],
            }
        )
    return parts


def compile_model(model: Model, dtype: str = "float32") -> Compiled:
    """Build indices, tables, PML, channels and probes. Raises :class:`ModelError`."""
    g = model.grid
    nx, ny, nz = g.n
    if len(model.walls) != 6 or any(w not in WALL_KINDS for w in model.walls):
        raise ModelError("FDTD_BAD_WALLS", f"walls must be six of {WALL_KINDS}")
    eps_min = min([1.0] + [r.eps_r for r in model.regions if not r.pec])
    dt = g.dt(model.courant, 299792458.0 / math.sqrt(eps_min))
    fast = not model.regions and not model.ports and not model.conductors
    taus = _pole_taus(model)
    eps_c, sig_c, del_c = _cell_arrays(model, taus)

    # per-edge (eps, sigma), PEC mask
    edge_eps: list[FloatArray] = []
    edge_sig: list[FloatArray] = []
    edge_del: list[list[FloatArray]] = []
    pec: list[npt.NDArray[np.bool_]] = []
    for c in range(3):
        if fast:
            edge_eps.append(np.ones((nz + 1, ny + 1, nx + 1)))
            edge_sig.append(np.zeros((nz + 1, ny + 1, nx + 1)))
            edge_del.append([])
        else:
            e, s, *ds = _edge_average(g, c, [eps_c, sig_c, *del_c])
            edge_eps.append(e)
            edge_sig.append(s)
            edge_del.append(ds)
        valid = np.zeros((nz + 1, ny + 1, nx + 1), dtype=bool)
        (x0, x1), (y0, y1), (z0, z1) = g.valid("E", c)
        valid[z0 - 1 : z1 - 1, y0 - 1 : y1 - 1, x0 - 1 : x1 - 1] = True
        pec.append(~valid)
    if not any(isinstance(p, ModePort) for p in model.ports) and not model.mode_probes:
        del eps_c, sig_c, del_c  # only the mode-port static solve reads the cell arrays again
    # Debye ADE: the static part of the polarisation current joins eps (eps_inf + dt beta / 2 eps0)
    for c in range(3):
        for pi, tau in enumerate(taus):
            edge_eps[c] += edge_del[c][pi] * (dt / (2.0 * float(tau) + dt))
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

    # copper boxes: PEC interior, surface-impedance face edges (applied by the engines)
    sibc = _compile_conductors(model, g, pec, dt)

    # lumped ports: loading conductivity and sources
    channels: list[dict[str, Any]] = []
    ports_info: list[dict[str, Any]] = []
    probes: list[dict[str, Any]] = []
    mode_cache: dict[Any, Any] = {}
    eps_mid: FloatArray | None = None
    for port in model.ports:
        if isinstance(port, ModePort):
            from oa_fdtd.ports.build import build_mode_port, solve_port_modes

            if eps_mid is None:  # real permittivity at the band centre for the static solve
                w_mid = 2.0 * np.pi * 0.5 * sum(model.band)
                eps_mid = eps_c.copy()
                for pi, tau in enumerate(taus):
                    eps_mid = eps_mid + del_c[pi] / (1.0 + (w_mid * float(tau)) ** 2)
            key = (port.axis, port.position, port.lo, port.hi, port.signals, port.direction)
            try:
                if key not in mode_cache:
                    mode_cache[key] = solve_port_modes(model, g, port, eps_mid[1:-1, 1:-1, 1:-1])
                modes, geo = mode_cache[key]
                mp = build_mode_port(model, g, port, modes, geo, edge_eps)
            except ValueError as exc:
                raise ModelError("FDTD_BAD_PORT", str(exc)) from exc
            pidx = len(ports_info)
            parts = _mode_probe_parts(g, port, mp, probes, edge_sig, load=True)
            ports_info.append(
                {
                    "name": port.name,
                    "axis": port.axis,
                    "r_ohm": mp["z"],
                    "sign": port.sign,
                    "flat": mp["flat"],
                    "channel": len(channels),
                    "sig_e": mp["sigma"],
                    "e_t": mp["template"],
                    "probe_parts": parts,
                    "eps_eff": mp["eps_eff"],
                    "voltages": mp["voltages"],
                }
            )
            channels.append(
                {"comp": mp["comp"], "flat": mp["flat"], "port": pidx, "sign": port.sign}
            )
            continue
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
    probe_sums: dict[str, list[str]] = {}
    for mpb in model.mode_probes:
        from oa_fdtd.ports.build import build_mode_port, solve_port_modes

        if eps_mid is None:
            w_mid = 2.0 * np.pi * 0.5 * sum(model.band)
            eps_mid = eps_c.copy()
            for pi, tau in enumerate(taus):
                eps_mid = eps_mid + del_c[pi] / (1.0 + (w_mid * float(tau)) ** 2)
        key = (mpb.axis, mpb.position, mpb.lo, mpb.hi, mpb.signals, mpb.direction)
        try:
            if key not in mode_cache:
                mode_cache[key] = solve_port_modes(model, g, mpb, eps_mid[1:-1, 1:-1, 1:-1])
            modes, geo = mode_cache[key]
            mp = build_mode_port(model, g, mpb, modes, geo, edge_eps)
        except ValueError as exc:
            raise ModelError("FDTD_BAD_PORT", str(exc)) from exc
        probe_sums[f"V:{mpb.name}"] = _mode_probe_parts(g, mpb, mp, probes, edge_sig, load=False)
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
    # per component: unique (eps, sigma) pairs as a 1-D complex key, then merge the small tables
    per_u: list[npt.NDArray[np.complex128]] = []
    per_inv: list[npt.NDArray[np.intp]] = []
    for c in range(3):
        keep = ~pec[c]
        ekey = np.round(edge_eps[c][keep], 12) + 1j * np.round(edge_sig[c][keep], 12)
        u_c, inv_c = np.unique(ekey, return_inverse=True)
        per_u.append(u_c)
        per_inv.append(inv_c.ravel())
        del ekey
    uniq_c, merge = np.unique(np.concatenate(per_u), return_inverse=True)
    merge = merge.ravel()
    uniq = np.stack([uniq_c.real, uniq_c.imag], axis=1)
    inv_parts = []
    off = 0
    for u_c, inv_c in zip(per_u, per_inv, strict=True):
        inv_parts.append(merge[off : off + u_c.size][inv_c])
        off += u_c.size
    if uniq.shape[0] + 1 > MAX_TABLE:
        raise ModelError(
            "FDTD_TOO_MANY_MATERIALS",
            f"{uniq.shape[0]} averaged materials exceed the uint16 table",
            "Use fewer distinct materials or a more uniform grid.",
        )
    eps_tab = np.concatenate([[0.0], uniq[:, 0]])
    sig_tab = np.concatenate([[0.0], uniq[:, 1]])
    for c in range(3):
        keep = ~pec[c]
        full = np.zeros((nz + 1, ny + 1, nx + 1), dtype=np.uint16)
        full[keep] = (inv_parts[c] + 1).astype(np.uint16)
        arr = np.zeros(g.shape, dtype=np.uint16)
        arr[1 : nz + 2, 1 : ny + 2, 1 : nx + 2] = full
        idx.append(arr)
    ep = eps_tab * EPS0
    loss = sig_tab * dt / (2.0 * np.where(ep > 0, ep, 1.0))
    ca_tab = np.where(eps_tab > 0, (1.0 - loss) / (1.0 + loss), 0.0)
    cb_tab = np.where(eps_tab > 0, dt / np.where(ep > 0, ep, 1.0) / (1.0 + loss), 0.0)

    ade = _compile_ade(g, dt, taus, edge_del, pec, idx, cb_tab)

    # injection amplitudes (per unit waveform) now that cb is known
    for ch in channels:
        flat = ch["flat"]
        if np.ndim(ch["comp"]) == 0:
            mat = idx[ch["comp"]].ravel()[flat]
        else:
            mat = np.zeros(flat.size, dtype=np.uint16)
            for cc in np.unique(ch["comp"]):
                sel = ch["comp"] == cc
                mat[sel] = idx[int(cc)].ravel()[flat[sel]]
        if "port" in ch:
            p = ports_info[ch["port"]]
            if "sig_e" in p:
                ch["amps"] = ch["sign"] * cb_tab[mat] * p["sig_e"] * p["e_t"]
            else:
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
        ade=ade,
        sibc=sibc,
        probe_sums=probe_sums,
    )
