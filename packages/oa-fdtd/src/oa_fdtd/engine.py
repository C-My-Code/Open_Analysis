# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""CUDA engine: fused Yee kernels through CuPy RawModule (ADR-0010 section 7).

This is the only oa_fdtd module that talks to CuPy; the module object comes from the oa_accel
backend (``backend.xp``), so ``import oa_fdtd.engine`` never imports cupy itself. The same
``kernels/yee.cu`` source is compiled by NVRTC (NVIDIA) or hiprtc (ROCm, untested on hardware).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from oa_fdtd.grid import EPS0, MU0
from oa_fdtd.model import Compiled

FloatArray = npt.NDArray[np.float64]
CHUNK = 64  # steps per probe buffer / CUDA graph
_SRC = (Path(__file__).parent / "kernels" / "yee.cu").read_text(encoding="utf-8")
_MODULES: dict[tuple[int, str, bool], Any] = {}


def estimate_bytes(c: Compiled, itemsize: int) -> int:
    """Device bytes: fields (6 x B), 6 B of index per cell, PML psi, +5 % for tables and buffers."""
    cells = int(np.prod(c.grid.shape))
    psi = sum(int(np.prod(corr.shape)) for corr in c.pml.corrections) * c.batch * itemsize
    return int((cells * (6 * itemsize * c.batch + 6) + psi) * 1.05)


def _module(xp: Any, dtype: str, uniform: bool) -> Any:
    key = (id(xp), dtype, uniform)
    if key not in _MODULES:
        opts = ["-std=c++14"]
        if dtype == "float64":
            opts.append("-DUSE_DOUBLE=1")
        if uniform:
            opts.append("-DUNIFORM=1")
        _MODULES[key] = xp.RawModule(code=_SRC, options=tuple(opts))
    return _MODULES[key]


def _grid3(n: tuple[int, int, int], block: tuple[int, int, int]) -> tuple[int, int, int]:
    return tuple(-(-n[a] // block[a]) for a in range(3))  # type: ignore[return-value]


class CupyEngine:
    """Same equations as :class:`oa_fdtd.reference.NumpyEngine`, on the GPU."""

    name = "cuda"
    BLOCK = (32, 4, 2)

    def __init__(
        self,
        c: Compiled,
        dtype: str = "float32",
        record_series: bool = False,
        xp: Any = None,
        max_steps: int = 200_000,
        use_graph: bool = True,
    ) -> None:
        if xp is None:
            from oa_accel import select

            xp = select("cuda").xp
        self.xp = xp
        self.c = c
        self.dtype = dtype
        self.np_dtype = np.dtype(dtype)
        self.record_series = record_series
        self.max_steps = max_steps
        g = c.grid
        b = c.batch
        self.B = b
        self.bs = int(np.prod(g.shape))
        shape = (b, *g.shape)
        x = xp
        self.E = [x.zeros(shape, self.np_dtype) for _ in range(3)]
        self.H = [x.zeros(shape, self.np_dtype) for _ in range(3)]
        self.idx = [x.asarray(i) for i in c.idx]
        self.ca = x.asarray(c.ca_tab.astype(self.np_dtype))
        self.cb = x.asarray(c.cb_tab.astype(self.np_dtype))
        self.ip = [x.asarray(g.inv_primal(a).astype(self.np_dtype)) for a in range(3)]
        self.idd = [x.asarray(g.inv_dual(a).astype(self.np_dtype)) for a in range(3)]
        self.dtmu = self.np_dtype.type(c.dt / MU0)
        self.fp = x.asarray(np.array([int(a.data.ptr) for a in (*self.E, *self.H)], np.uint64))
        self.step_dev = x.zeros(1, np.int32)
        self.step_count = 0
        self._groups = self._z_groups()
        self._build_lists()
        self._build_pml()
        self.pmc = [(a, s) for a in range(3) for s in (0, 1) if c.walls[2 * a + s] == "pmc"]
        self._graph: Any = None
        self.use_graph = use_graph
        self._series: dict[str, list[FloatArray]] = {"E": [], "H": []}
        nf = c.frequencies.size
        self.omega = 2.0 * np.pi * c.frequencies
        self.acc = {k: x.zeros((nf, b, len(p)), np.complex128) for k, p in self._probes.items()}

    # ---- setup
    def _z_groups(self) -> list[tuple[int, int, int | None, list[tuple[int, Any]]]]:
        """Consecutive z planes ``[k0, k1)`` with ``(uniform_material or None, pec_lists)``."""
        c, g = self.c, self.c.grid
        nx, ny, nz = g.n
        planes: list[tuple[int | None, list[np.ndarray[Any, Any]]]] = []
        for k in range(1, nz + 2):
            vals: set[int] = set()
            pec_n = 0
            lists = []
            for ci in range(3):
                sl = c.idx[ci][k, 1 : ny + 2, 1 : nx + 2]
                z = sl == 0
                pec_n += int(z.sum())
                nzv = np.unique(sl[~z])
                vals.update(int(v) for v in nzv)
                jj, ii = np.nonzero(z)
                lists.append(((k * g.shape[1] + jj + 1) * g.shape[2] + ii + 1).astype(np.int64))
            total = 3 * (nx + 1) * (ny + 1)
            uni = next(iter(vals)) if len(vals) == 1 and pec_n <= 0.1 * total else None
            planes.append((uni, lists))
        groups = []
        k = 0
        while k < len(planes):
            u = planes[k][0]
            k2 = k
            while k2 + 1 < len(planes) and planes[k2 + 1][0] == u:
                k2 += 1
            pec = []
            if u is not None:
                for ci in range(3):
                    flat = np.concatenate([planes[kk][1][ci] for kk in range(k, k2 + 1)])
                    if flat.size:
                        pec.append((ci, flat))
            groups.append((k + 1, k2 + 2, u, pec))
            k = k2 + 1
        return groups

    def _build_lists(self) -> None:
        x, c = self.xp, self.c
        self._zero: list[tuple[Any, Any, int] | None] = []
        for _k0, _k1, u, pec in self._groups:
            if u is None or not pec:
                self._zero.append(None)
                continue
            zc = np.concatenate([np.full(f.size, ci, np.int32) for ci, f in pec])
            zf = np.concatenate([f for _, f in pec])
            self._zero.append((x.asarray(zc), x.asarray(zf), zf.size))
        # sources
        comp: list[Any] = []
        flat: list[Any] = []
        amp: list[Any] = []
        chan: list[Any] = []
        for ci, ch in enumerate(c.channels):
            comp.append(np.full(ch["flat"].size, ch["comp"], np.int32))
            flat.append(ch["flat"].astype(np.int64))
            amp.append(np.asarray(ch["amps"], np.float64))
            chan.append(np.full(ch["flat"].size, ci, np.int32))
        self._inj = None
        if comp:
            self.n_inj = int(sum(a.size for a in amp))
            self._inj = tuple(x.asarray(np.concatenate(v)) for v in (comp, flat, amp, chan))
            self.drive = x.asarray(c.drive.astype(np.float64))
            nsteps = self.max_steps
            wave = np.zeros((len(c.channels), nsteps))
            t = (np.arange(nsteps) + 0.5) * c.dt
            for ci, ch in enumerate(c.channels):
                wave[ci] = ch["waveform"](t)
            self.wave = x.asarray(wave)
        # probes: per kind concatenated
        self._probes: dict[str, list[dict[str, Any]]] = {
            "E": [p for p in c.probes if p["kind"] == "E"],
            "H": [p for p in c.probes if p["kind"] == "H"],
        }
        self._pdev: dict[str, Any] = {}
        for kind, plist in self._probes.items():
            if not plist:
                continue
            off = np.concatenate([[0], np.cumsum([p["flat"].size for p in plist])]).astype(np.int64)
            pcomp = np.array([p["comp"] + (0 if kind == "E" else 3) for p in plist], np.int32)
            self._pdev[kind] = (
                x.asarray(pcomp),
                x.asarray(off),
                x.asarray(np.concatenate([p["flat"] for p in plist]).astype(np.int64)),
                x.asarray(np.concatenate([p["weights"] for p in plist]).astype(np.float64)),
                x.zeros((CHUNK, self.B, len(plist)), np.float64),
            )

    def _build_pml(self) -> None:
        x, c = self.xp, self.c
        dt = self.np_dtype
        self._co = {
            a: {
                k: x.asarray(getattr(co, k).astype(dt))
                for k in ("eb", "ec", "ek", "hb", "hc", "hk")
            }
            for a, co in c.pml.coeffs.items()
        }
        self._psi = [x.zeros((self.B, *corr.shape), dt) for corr in c.pml.corrections]

    # ---- launches
    def _launch_step(self) -> None:
        c, g = self.c, self.c.grid
        nx, ny, nz = g.n
        X, Y = g.shape[2], g.shape[1]
        dt = self.np_dtype
        block = self.BLOCK
        m_idx = _module(self.xp, self.dtype, False)
        m_uni = _module(self.xp, self.dtype, True)
        nk = nz + 1
        grid = _grid3((nx + 1, ny + 1, nk * self.B), block)
        m_idx.get_function("h_update")(
            grid, block,
            (*self.H, *self.E, *self.ip, self.dtmu, np.int32(nx), np.int32(ny), np.int32(nz),
             np.int32(X), np.int32(Y), np.int64(self.bs), np.int32(self.B)),
        )  # fmt: skip
        self._pml_launch("H")
        self._ghosts()
        self._record("H")
        for gi, (k0, k1, u, _pec) in enumerate(self._groups):
            n_k = k1 - k0
            grid_e = _grid3((nx + 1, ny + 1, n_k * self.B), block)
            uni = u is not None
            mod = m_uni if uni else m_idx
            ca0 = dt.type(c.ca_tab[u]) if u is not None else dt.type(0)
            cb0 = dt.type(c.cb_tab[u]) if u is not None else dt.type(0)
            mod.get_function("e_update")(
                grid_e, block,
                (*self.E, *self.H, *self.idx, self.ca, self.cb, *self.idd, ca0, cb0,
                 np.int32(nx), np.int32(ny), np.int32(k0), np.int32(n_k), np.int32(X),
                 np.int32(Y), np.int64(self.bs), np.int32(self.B)),
            )  # fmt: skip
            z = self._zero[gi]
            if z is not None:
                m_idx.get_function("zero_list")(
                    (-(-z[2] // 256),),
                    (256,),
                    (self.fp, z[0], z[1], np.int32(z[2]), np.int64(self.bs), np.int32(self.B)),
                )
        self._pml_launch("E")
        if self._inj is not None:
            comp, flat, amp, chan = self._inj
            m_idx.get_function("inject")(
                (-(-self.n_inj // 256),), (256,),
                (self.fp, comp, flat, amp, chan, self.drive, self.wave, self.step_dev,
                 np.int32(self.n_inj), np.int32(len(c.channels)), np.int32(self.max_steps),
                 np.int64(self.bs), np.int32(self.B)),
            )  # fmt: skip
        self._record("E")
        m_idx.get_function("bump")((1,), (1,), (self.step_dev,))

    def _pml_launch(self, kind: str) -> None:
        c, g = self.c, self.c.grid
        X, Y = g.shape[2], g.shape[1]
        mod = _module(self.xp, self.dtype, False)
        dt = self.np_dtype
        for corr, psi in zip(c.pml.corrections, self._psi, strict=True):
            if corr.kind != kind:
                continue
            co = self._co[corr.a]
            src = (self.H if kind == "E" else self.E)[corr.b]
            fld = (self.E if kind == "E" else self.H)[corr.c]
            (i0, i1), (j0, j1), (k0, k1) = corr.box
            ni, nj, nk = i1 - i0, j1 - j0, k1 - k0
            total = ni * nj * nk * self.B
            ip = self.idd[corr.a] if kind == "E" else self.ip[corr.a]
            b_, c_, k_ = (
                (co["eb"], co["ec"], co["ek"]) if kind == "E" else (co["hb"], co["hc"], co["hk"])
            )
            mod.get_function("cpml")(
                (-(-total // 256),), (256,),
                (fld, src, psi, self.idx[corr.c], self.cb, ip, b_, c_, k_,
                 np.int32(kind == "E"), np.int32(corr.a), dt.type(corr.sign), self.dtmu,
                 np.int32(i0), np.int32(j0), np.int32(k0), np.int32(ni), np.int32(nj),
                 np.int32(nk), np.int32(X), np.int32(Y), np.int64(self.bs), np.int32(self.B)),
            )  # fmt: skip

    def _ghosts(self) -> None:
        g = self.c.grid
        for a, s in self.pmc:
            for comp in range(3):
                if comp == a:
                    continue
                h = self.H[comp]
                ax = 3 - a
                dst, src = (0, 1) if s == 0 else (g.n[a] + 1, g.n[a])
                d_sl: list[Any] = [slice(None)] * 4
                s_sl: list[Any] = [slice(None)] * 4
                d_sl[ax], s_sl[ax] = dst, src
                self.xp.negative(h[tuple(s_sl)], out=h[tuple(d_sl)])

    def _record(self, kind: str) -> None:
        if kind not in self._pdev:
            return
        pcomp, off, flat, wgt, buf = self._pdev[kind]
        mod = _module(self.xp, self.dtype, False)
        n_p = len(self._probes[kind])
        mod.get_function("probe_sum")(
            (n_p, self.B), (256,),
            (self.fp, pcomp, off, flat, wgt, buf, self.step_dev, np.int32(CHUNK),
             np.int32(n_p), np.int32(self.B), np.int64(self.bs)),
            shared_mem=256 * 8,
        )  # fmt: skip

    def _capture(self) -> None:
        x = self.xp
        try:
            stream = x.cuda.Stream(non_blocking=True)
            with stream:
                stream.begin_capture()
                for _ in range(CHUNK):
                    self._launch_step()
                self._graph = stream.end_capture()
            # the capture ran nothing; rewind the counter that the capture never advanced
        except Exception:  # capture unsupported (HIP, driver): eager launches
            self._graph = False

    def _flush(self, first_step: int, count: int) -> None:
        x, c = self.xp, self.c
        n0 = first_step
        for kind, (_pc, _o, _f, _w, buf) in self._pdev.items():
            off = 0.5 if kind == "H" else 1.0
            steps = np.arange(n0, n0 + count)
            t = (steps + off) * c.dt
            slots = steps % CHUNK
            ph = np.exp(-1j * self.omega[:, None] * t[None, :]) * c.dt  # (F, count)
            vals = buf[x.asarray(slots)]  # (count, B, P)
            acc = x.asarray(ph) @ vals.reshape(count, -1).astype(np.complex128)
            self.acc[kind] += acc.reshape(self.omega.size, self.B, -1)
            if self.record_series:
                self._series[kind].append(x.asnumpy(vals))

    def step(self, n: int = 1) -> None:
        if self.step_count + n > self.max_steps:
            raise ValueError("step count exceeds max_steps (wave table length)")
        done = 0
        while done < n:
            start = self.step_count
            take = min(n - done, CHUNK - start % CHUNK)
            if self.use_graph and take == CHUNK and self._graph is None:
                # first full chunk: capture then run it (capture itself executes nothing)
                self._capture()
            if self._graph and take == CHUNK:
                self._graph.launch()
            else:
                for _ in range(take):
                    self._launch_step()
            self.step_count += take
            done += take
            if self._pdev:
                self._flush(start, take)
        self.xp.cuda.Device().synchronize()

    # ---- observables
    def energy(self) -> float:
        x, c, g = self.xp, self.c, self.c.grid
        red_e = x.ReductionKernel(
            "T f, uint16 m, raw float64 tab, float64 wx, float64 wy, float64 wz",
            "float64 y",
            "(double)f * f * tab[m] * wx * wy * wz",
            "a + b",
            "y = a",
            "0",
            "oa_fdtd_energy_e",
        )
        red_h = x.ReductionKernel(
            "T f, float64 wx, float64 wy, float64 wz",
            "float64 y",
            "(double)f * f * wx * wy * wz",
            "a + b",
            "y = a",
            "0",
            "oa_fdtd_energy_h",
        )
        tab = x.asarray(c.eps_tab * EPS0)
        total = 0.0
        for ci in range(3):
            w = [self._w1d("E", ci, a) for a in range(3)]
            total += 0.5 * float(red_e(self.E[ci], self.idx[ci][None], tab, *w))
            w = [self._w1d("H", ci, a) for a in range(3)]
            total += 0.5 * MU0 * float(red_h(self.H[ci], *w))
        del g
        return total

    def _w1d(self, kind: str, ci: int, a: int) -> Any:
        g = self.c.grid
        node = (a != ci) if kind == "E" else (a == ci)
        w = g.node_weights(a) if node else g.cell_weights(a)
        shape = [1, 1, 1, 1]
        shape[3 - a] = w.size
        return self.xp.asarray(w.reshape(shape))

    def get_fields(self) -> dict[str, FloatArray]:
        """Host copies ``{"Ex": (B, z, y, x), ...}`` in float64 (tests, viewers)."""
        names = ("Ex", "Ey", "Ez", "Hx", "Hy", "Hz")
        return {
            n: self.xp.asnumpy(a).astype(np.float64)
            for n, a in zip(names, (*self.E, *self.H), strict=True)
        }

    def results(self) -> dict[str, FloatArray]:
        out: dict[str, FloatArray] = {}
        for kind, plist in self._probes.items():
            if not plist:
                continue
            a = self.xp.asnumpy(self.acc[kind])
            for pi, p in enumerate(plist):
                out[p["name"]] = a[:, :, pi].T.copy()
        return out

    def series(self) -> dict[str, FloatArray]:
        out: dict[str, FloatArray] = {}
        for kind, plist in self._probes.items():
            if plist and self._series[kind]:
                arr = np.concatenate(self._series[kind], axis=0)  # (steps, B, P)
                for pi, p in enumerate(plist):
                    out[p["name"]] = arr[:, :, pi].T.copy()
        return out

    def peak_bytes(self) -> int:
        return int(self.xp.get_default_memory_pool().total_bytes())
