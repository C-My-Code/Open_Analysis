# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``oa-fdtd run job.json``: the engine-level job format (no ``oa_framework`` import).

Job (JSON)::

    {"grid": {"x": [...], "y": [...], "z": [...]},          # mesh lines [m]
     "waveform": {"kind": "gauss_mod", "f0": 5e9, "bandwidth": 10e9},
     "frequencies": [1e9, ...],                              # [Hz]
     "walls": ["pec", "pec", "cpml", "cpml", "pmc", "pmc"],  # xlo xhi ylo yhi zlo zhi
     "cpml": {"cells": 10}, "regions": [{"lo": [..], "hi": [..], "eps_r": 4.4, "sigma": 0,
     "pec": false}], "ports": [{"name": "p1", "axis": 2, "lo": [..], "hi": [..], "r_ohm": 50}],
     "sources": [...], "probes": [...],
     "run": {"engine": "auto", "dtype": "float32", "max_steps": 100000, "end_db": -50},
     "output": "ports.npz"}

Writes ``output`` (relative to the job file) with ``frequencies``, ``s`` (when ports exist),
and ``probe_<name>``; prints a JSON summary. Failures print an ``ErrorInfo``-shaped object
(``code``, ``message``, ``path``, ``hint``) and exit with status 2.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

from oa_fdtd.grid import Grid
from oa_fdtd.model import (
    LumpedPort,
    Model,
    ModelError,
    Probe,
    Region,
    SoftSource,
    Waveform,
)
from oa_fdtd.pml import CpmlSpec
from oa_fdtd.run import run, sparams


def _t3(v: Any) -> tuple[float, float, float]:
    a = [float(x) for x in v]
    if len(a) != 3:
        raise ModelError("FDTD_BAD_JOB", "boxes need three coordinates")
    return (a[0], a[1], a[2])


def model_from_job(job: dict[str, Any]) -> Model:
    """Build a :class:`Model` from a parsed job. Raises :class:`ModelError`."""
    try:
        g = Grid(*(np.asarray(job["grid"][k], dtype=np.float64) for k in "xyz"))
        wf = Waveform(**job["waveform"])
        m = Model(g, wf, np.asarray(job["frequencies"], dtype=np.float64))
        if "walls" in job:
            m.walls = tuple(job["walls"])
        if "cpml" in job:
            m.cpml = CpmlSpec(**job["cpml"])
        if "courant" in job:
            m.courant = float(job["courant"])
        for r in job.get("regions", []):
            m.regions.append(
                Region(
                    _t3(r["lo"]),
                    _t3(r["hi"]),
                    r.get("eps_r", 1.0),
                    r.get("sigma", 0.0),
                    r.get("pec", False),
                )
            )
        for p in job.get("ports", []):
            m.ports.append(
                LumpedPort(
                    p["name"],
                    int(p["axis"]),
                    _t3(p["lo"]),
                    _t3(p["hi"]),
                    p.get("r_ohm", 50.0),
                    p.get("sign", 1.0),
                )
            )
        for s in job.get("sources", []):
            m.sources.append(
                SoftSource(
                    s["name"], int(s["axis"]), _t3(s["lo"]), _t3(s["hi"]), s.get("amplitude", 1.0)
                )
            )
        for p in job.get("probes", []):
            m.probes.append(
                Probe(
                    p["name"],
                    p["kind"],
                    int(p["axis"]),
                    _t3(p["lo"]),
                    _t3(p["hi"]),
                    p.get("weight", "sum"),
                )
            )
    except (KeyError, TypeError) as exc:
        raise ModelError(
            "FDTD_BAD_JOB", f"malformed job: {exc!r}", "See oa_fdtd.cli docstring."
        ) from exc
    return m


def run_job(path: Path) -> dict[str, Any]:
    job = json.loads(path.read_text(encoding="utf-8"))
    model = model_from_job(job)
    opts = job.get("run", {})
    res = run(
        model,
        engine=opts.get("engine", "auto"),
        dtype=opts.get("dtype", "float32"),
        max_steps=int(opts.get("max_steps", 100_000)),
        end_db=opts.get("end_db", -50.0),
    )
    out: dict[str, Any] = {"frequencies": model.frequencies}
    if model.ports:
        out["s"] = sparams(model, res)
    for name, v in res.probes.items():
        out["probe_" + name.replace(":", "_")] = v
    target = path.parent / job.get("output", "ports.npz")
    np.savez(target, **out)
    return {
        "output": str(target),
        "steps": res.steps,
        "stopped_by": res.stopped_by,
        "engine": res.engine,
        "dtype": res.dtype,
        "energy_db": res.energy_db,
        "dt_s": res.dt,
        "peak_gpu_bytes": res.peak_bytes,
        "accel": res.report,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="oa-fdtd")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run a job file")
    r.add_argument("job", type=Path)
    args = ap.parse_args(argv)
    try:
        summary = run_job(args.job)
    except ModelError as exc:
        print(
            json.dumps(
                {"code": exc.code, "message": exc.message, "path": str(args.job), "hint": exc.hint}
            )
        )
        return 2
    except OSError as exc:
        print(
            json.dumps({"code": "FDTD_IO", "message": str(exc), "path": str(args.job), "hint": ""})
        )
        return 2
    except Exception as exc:  # AccelError and friends carry an ErrorInfo
        info = getattr(exc, "info", None)
        d = (
            info.to_dict()
            if info is not None
            else {"code": "FDTD_ERROR", "message": str(exc), "path": "", "hint": ""}
        )
        print(json.dumps(d))
        return 2
    print(json.dumps(summary, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
