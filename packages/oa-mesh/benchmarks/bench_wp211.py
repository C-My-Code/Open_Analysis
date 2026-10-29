# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""WP-211 benchmarks (run by hand, never collected by pytest).

    uv run python packages/oa-mesh/benchmarks/bench_wp211.py <case> [key=value ...]

Cases: ``plate`` (the pi.dc 3e5-unknown plate), ``plane`` (power_plane board through pi.dc),
``layers8`` (synthetic 8-layer board), ``algos`` (2D algorithm table), ``solid3d`` (3D solid vs
sheet copper, quality). Options: ``workers=N`` (<= 4), ``algorithm=...``, ``h_far=...``,
``algo3d=...``. Each case prints one ``[bench]`` line with wall time and the peak resident memory
of this process plus its worker processes (sampled every 50 ms).
"""

from __future__ import annotations

import inspect
import sys
import threading
import time
from pathlib import Path
from typing import Any

import psutil

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "packages" / "oa-mesh" / "tests"))
sys.path.insert(0, str(REPO / "packages" / "oa-pi" / "tests"))

from mesh_helpers import circle, piece, poly, random_centres, rect  # noqa: E402

from oa_mesh import Contact, SizeField, mesh_copper_sheets  # noqa: E402

_SUPPORTED = set(inspect.signature(mesh_copper_sheets).parameters)


class PeakMem:
    """Peak total RSS (GB) of this process and its children."""

    def __enter__(self) -> PeakMem:
        self.peak = 0.0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        return self

    def _run(self) -> None:
        me = psutil.Process()
        while not self._stop.is_set():
            try:
                procs = [me, *me.children(recursive=True)]
                rss = sum(p.memory_info().rss for p in procs)
                self.peak = max(self.peak, rss / 2**30)
            except psutil.Error:
                pass
            self._stop.wait(0.05)

    def __exit__(self, *a: object) -> None:
        self._stop.set()
        self._t.join()


def _kw(opts: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in ("workers", "algorithm", "h_far", "threads"):
        if k in opts:
            if k not in _SUPPORTED:
                raise SystemExit(f"mesh_copper_sheets has no '{k}' (baseline code)")
            v: Any = opts[k]
            if k in ("workers", "threads"):
                v = int(v)
            elif k == "h_far":
                v = float(v)
            out[k] = v
    return out


def _report(name: str, dt: float, mem: PeakMem, **extra: Any) -> None:
    more = " ".join(f"{k}={v}" for k, v in extra.items())
    print(f"[bench] {name}: {dt:.2f} s, peak {mem.peak:.2f} GB {more}", flush=True)


def case_plate(opts: dict[str, str]) -> None:
    from pi_helpers import make_network, make_pin, make_sheet
    from shapely import Polygon as ShPolygon

    from oa_pi.dc.fem import solve_fem

    side = 0.1
    plate = ShPolygon([(0, 0), (side, 0), (side, side), (0, side)])
    p1 = ShPolygon([(0, 0), (1e-3, 0), (1e-3, side), (0, side)])
    p2 = ShPolygon([(side - 1e-3, 0), (side, 0), (side, side), (side - 1e-3, side)])
    net = make_network(
        [make_sheet("L1", "N", [plate])],
        make_pin("p1", "J1", "L1", "N", p1),
        make_pin("p2", "J2", "L1", "N", p2),
        h_plane=0.2e-3,
    )
    with PeakMem() as mem:
        t0 = time.perf_counter()
        sol = solve_fem(net)
        dt = time.perf_counter() - t0
    tm = {k: round(v, 2) for k, v in sol.timings.items()}
    _report("plate 3e5", dt, mem, unknowns=sol.n_unknowns, timings=tm)


def case_plane(opts: dict[str, str]) -> None:
    from pi_helpers import PLANE_CFG, read_kicad

    import oa_pi.dc.fem as fem
    from oa_framework.registry import Registry  # noqa: F401
    from oa_geometry import BoardGeometry
    from oa_model.setup import AnalysisSetup
    from oa_pi import PiDcAnalysis
    from oa_testkit.harness import run_inline_full

    kw = _kw(opts)
    real = fem.mesh_copper_sheets
    stats: dict[str, Any] = {}

    def timed(*a: Any, **k: Any) -> Any:
        t0 = time.perf_counter()
        out = real(*a, **k, **kw)
        stats["mesh_s"] = time.perf_counter() - t0
        stats["pieces"] = len(out)
        stats["tris"] = sum(m.quality.n_triangles for m in out)
        stats["hashes"] = ",".join(m.content_hash()[:6] for m in out)
        return out

    fem.mesh_copper_sheets = timed
    setup = AnalysisSetup(schema="oa-setup/1", name="dc", analysis="pi.dc", config=PLANE_CFG)
    board = read_kicad("power_plane")
    with PeakMem() as mem:
        t0 = time.perf_counter()
        run_inline_full(PiDcAnalysis, board, setup, geometry=BoardGeometry.build)
        dt = time.perf_counter() - t0
    fem.mesh_copper_sheets = real
    _report(
        "power_plane pi.dc",
        dt,
        mem,
        **{k: (f"{v:.2f}" if isinstance(v, float) else v) for k, v in stats.items()},
    )


def layers8() -> list[Any]:
    w, h = 80e-3, 60e-3
    pieces = []
    for li in range(8):
        cs = random_centres(360, w, h, 3e-3, 2.2e-3, seed=11 + li)
        holes = [circle(x, y, 0.45e-3, 24, cw=True) for x, y in cs[:200]]
        contacts = [
            Contact(id=f"v{i}", x=x, y=y, radius=0.3e-3, segments=24)
            for i, (x, y) in enumerate(cs[200:])
        ]
        pc = piece([poly(rect(0, 0, w, h), *holes)], contacts, pid=f"L{li + 1}:NET")
        pieces.append(pc)
    return pieces


def case_layers8(opts: dict[str, str]) -> None:
    pieces = layers8()
    sf = [SizeField(kind="contacts", size=0.15e-3)]
    kw = _kw(opts)
    with PeakMem() as mem:
        t0 = time.perf_counter()
        out = mesh_copper_sheets(pieces, h_default=2e-3, size_fields=sf, **kw)
        dt = time.perf_counter() - t0
    tris = sum(m.quality.n_triangles for m in out)
    amin = min(m.quality.min_angle_deg for m in out)
    hs = ",".join(m.content_hash()[:6] for m in out)
    _report("8-layer synthetic", dt, mem, tris=tris, min_angle=round(amin, 1), hashes=hs)


def case_algos(opts: dict[str, str]) -> None:
    if opts.get("plate"):  # the pi.dc 3e5-unknown plate: 100 x 100 mm, h = 0.2 mm
        pc = piece([poly(rect(0, 0, 0.1, 0.1))])
        sf = []
        h_def = 0.2e-3
    else:
        w, h = 100e-3, 80e-3
        cs = random_centres(900, w, h, 3e-3, 2.2e-3, seed=7)
        holes = [circle(x, y, 0.45e-3, 24, cw=True) for x, y in cs[:600]]
        contacts = [
            Contact(id=f"v{i}", x=x, y=y, radius=0.3e-3, segments=24)
            for i, (x, y) in enumerate(cs[600:])
        ]
        pc = piece([poly(rect(0, 0, w, h), *holes)], contacts)
        sf = [SizeField(kind="contacts", size=0.15e-3)]
        h_def = 2e-3
    print("algorithm | time s | triangles | min angle | max aspect")
    for algo in opts.get("list", "frontal_delaunay,delaunay,meshadapt").split(","):
        if "algorithm" not in _SUPPORTED:
            raise SystemExit("baseline code has no algorithm option")
        t0 = time.perf_counter()
        (m,) = mesh_copper_sheets([pc], h_default=h_def, size_fields=sf, algorithm=algo)
        dt = time.perf_counter() - t0
        q = m.quality
        print(f"{algo} | {dt:.2f} | {q.n_triangles} | {q.min_angle_deg:.1f} | {q.max_aspect:.2f}")


def case_solid3d(opts: dict[str, str]) -> None:
    import tempfile

    from oa_geometry import BoardGeometry, RegionSpec
    from oa_mesh.occ3d import OccOptions, build_occ_model, write_msh
    from oa_model.em import MeshSettings
    from oa_testkit.builders import via_transition_board

    geo = BoardGeometry.build(via_transition_board())
    cut = geo.cut_region(RegionSpec(nets=("SIG",), bbox=(5e-3, -4e-3, 20e-3, 4e-3)))
    expert: dict[str, Any] = {}
    for k in ("algorithm3d", "optimize", "num_threads"):
        if k in opts:
            expert[f"gmsh.{k}"] = int(opts[k]) if opts[k].isdigit() else opts[k]
    for thr in [float(opts["thr"])] if "thr" in opts else (0.0, 0.1e-3):
        with PeakMem() as mem, tempfile.TemporaryDirectory() as d:
            t0 = time.perf_counter()
            model = build_occ_model(
                cut.structure, sheet_threshold=thr, options=OccOptions(f_max=10e9)
            )
            s = write_msh(
                model, Path(d) / "v.msh", sizes=MeshSettings(preset="medium", expert=expert)
            )
            dt = time.perf_counter() - t0
        _report(
            f"3d {'solid' if thr == 0 else 'sheet'}",
            dt,
            mem,
            tets=s.n_tets,
            min_sicn=round(s.min_sicn, 4),
            min_gamma=round(s.min_gamma, 4),
        )


if __name__ == "__main__":
    case = sys.argv[1]
    options = dict(a.split("=", 1) for a in sys.argv[2:])
    {
        "plate": case_plate,
        "plane": case_plane,
        "layers8": case_layers8,
        "algos": case_algos,
        "solid3d": case_solid3d,
    }[case](options)
