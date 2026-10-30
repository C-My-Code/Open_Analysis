# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Palace on the canonical microstrip (B-EM-01) at chosen settings, for the R1 comparison.

``uv run python tests/benchmarks/references/xs/run_palace_compare.py --order 2 --eps-freq 10e9
--model sheet --f 5e9 10e9`` runs one Palace job (WSL, boxed by ``oa-palace-run``: 12 GB, 800 % CPU)
and appends a record to ``R1/palace_runs.json``. Wave ports on the x faces, ``y_half`` / ``air``
padding of the absorbing boundary, dielectric evaluated at ``--eps-freq`` (Palace 0.18 has no
frequency-dependent materials). Heavy: hold the token, run in the foreground, then ``wsl --shutdown``.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import refspec as rs

from oa_em_palace import PalaceAdapter, discover
from oa_jobs.runners import make_runner
from oa_jobs.tools import ToolsConfig
from oa_model.em import EMWavePort, FrequencyPlan
from oa_model.materials import DielectricMaterial
from oa_testkit.emproblems import microstrip_thru_problem

OUT = Path(__file__).parent / "R1" / "palace_runs.json"


class _Never:
    def is_cancelled(self) -> bool:
        return False


def problem(args: argparse.Namespace) -> Any:
    p = microstrip_thru_problem(
        length=args.length * 1e-3, y_half=args.y_half * 1e-3, air=args.air * 1e-3
    )
    mats = tuple(
        m.model_copy(update={"model": rs.DS_MODEL}) if isinstance(m, DielectricMaterial) else m
        for m in p.structure.materials
    )
    box = p.structure.bbox
    trace = next(c for c in p.structure.conductors if c.role == "signal")
    ports = tuple(
        EMWavePort(
            name=name,
            face=face,  # type: ignore[arg-type]
            rect=(box.ymin, box.ymax, box.zmin, box.zmax),
            line_type="microstrip",
            signal_conductors=(trace.id,),
            z0=None,
        )
        for name, face in (("P1", "xmin"), ("P2", "xmax"))
    )
    expert: dict[str, Any] = {
        "palace.conductor_model": args.model,
        "palace.eps_freq": args.eps_freq,
        "palace.solver": args.solver,
        "gmsh.cells_per_wavelength": args.cpw,
    }
    if args.hmax:
        expert["gmsh.h_max"] = args.hmax
    mesh = p.mesh.model_copy(
        update={"preset": args.preset, "fem_order": args.order, "expert": expert}
    )
    f = sorted(args.f)
    freq = FrequencyPlan(f_min=f[0], f_max=f[-1], n_points=len(f))
    return p.model_copy(
        update={
            "structure": p.structure.model_copy(update={"materials": mats}),
            "ports": ports,
            "name": "microstrip_thru_25mm_wave",
            "mesh": mesh,
            "frequency": freq,
        }
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--order", type=int, default=2)
    ap.add_argument("--eps-freq", type=float, default=10e9)
    ap.add_argument("--model", default="sheet", choices=["sheet", "pec"])
    ap.add_argument("--preset", default="coarse")
    ap.add_argument("--solver", default="AMS")
    ap.add_argument("--cpw", type=float, default=3.0, help="gmsh cells per wavelength")
    ap.add_argument("--f", type=float, nargs="+", default=[5e9, 10e9])
    ap.add_argument("--y-half", type=float, default=5.0, help="lateral half width [mm]")
    ap.add_argument("--air", type=float, default=6.0, help="air above the board [mm]")
    ap.add_argument("--hmax", type=float, default=0.0, help="gmsh size cap [mm], 0 = none")
    ap.add_argument("--length", type=float, default=25.0, help="line length [mm]")
    ap.add_argument("--max-tets", type=int, default=400000)
    ap.add_argument("--ranks", type=int, default=4)
    ap.add_argument("--mem-gb", type=float, default=12.0)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    info = discover()
    if info is None:
        raise SystemExit("Palace not found (packages/oa-em-palace/README.md)")
    prob = problem(args)
    work = Path(tempfile.mkdtemp(prefix="palace_wp510_"))
    adapter = PalaceAdapter(mpi_ranks=args.ranks, memory_gb=args.mem_gb, cpu_percent=800)
    run = adapter.prepare(prob, work, info)
    print("tets", run.mesh_stats.get("cells"), "dofs", run.mesh_stats.get("dofs"), flush=True)
    if (run.mesh_stats.get("cells") or 0) > args.max_tets:
        raise SystemExit("mesh too large for the box; shrink the domain or the line")
    errs = [d for d in adapter.check(prob) if d.severity == "error"]
    assert not errs, errs
    tools = ToolsConfig({"palace": {"runner": info.runner, "path": str(info.path)}})
    t0 = time.time()
    res = make_runner(info.runner, tools).run(run.commands[0], lambda f, m: None, _Never())
    out = adapter.collect(prob, run, [res])
    assert out.network is not None, [w.message for w in out.warnings]
    meta = json.loads((work / "out" / f"{prob.name}.json").read_text(encoding="utf-8"))
    f = np.asarray(out.network.freqs.array)
    s = np.asarray(out.network.s.array)
    rec = {
        "tag": args.tag,
        "args": {k: v for k, v in vars(args).items()},
        "freqs_hz": f.tolist(),
        "il_db": (-20 * np.log10(np.abs(s[:, 1, 0]))).tolist(),
        "s11_db": (20 * np.log10(np.abs(s[:, 0, 0]))).tolist(),
        "s21_phase_deg": np.degrees(np.angle(s[:, 1, 0])).tolist(),
        "wall_s": meta.get("wall_s"),
        "peak_mem_mb": meta.get("peak_mem_mb"),
        "tets": run.mesh_stats.get("cells"),
        "dofs": run.mesh_stats.get("dofs"),
        "dielectric_fit": meta.get("dielectric_fit"),
        "workdir": str(work),
        "elapsed_s": round(time.time() - t0, 1),
    }
    print(json.dumps({k: v for k, v in rec.items() if k != "args"}))
    runs = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else []
    runs.append(rec)
    OUT.write_text(json.dumps(runs, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
