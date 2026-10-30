# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``python -m oa_fdtd.adapter.worker problem.json``: the command the adapter prepares.

Reads ``problem.json`` (an ``EMProblem``) from the working directory, solves it with every
excitation batched (:func:`oa_fdtd.adapter.sim.solve_problem`) and writes ``ports.npz``
(``freqs``, ``s`` as ``(F, N, N)``, ``z0``, ``names``) and ``run.json``. Progress lines are
``step <n>`` on stdout; errors are one ``ErrorInfo``-shaped JSON line and exit status 2.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from oa_fdtd.adapter.sim import options_from_problem, run_meta, solve_problem
from oa_model.em import EMProblem


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="oa_fdtd.adapter.worker")
    ap.add_argument("problem", type=Path)
    args = ap.parse_args(argv)
    here = args.problem.resolve().parent
    try:
        problem = EMProblem.model_validate_json(args.problem.read_text(encoding="utf-8"))
        opts = options_from_problem(problem)
        r = solve_problem(problem, opts)
    except Exception as exc:
        info = getattr(exc, "info", None)
        d = (
            info.to_dict()
            if info is not None
            else {
                "code": getattr(exc, "code", "FDTD.ERROR"),
                "message": getattr(exc, "message", str(exc)),
                "path": str(args.problem),
                "hint": getattr(exc, "hint", ""),
            }
        )
        print(json.dumps(d))
        return 2
    np.savez(here / "ports.npz", freqs=r.freqs, s=r.s, z0=r.z0, names=np.array(r.port_names))
    meta = run_meta(r)
    meta["fdtd_version"] = "oa-fdtd 0.1.0"
    meta["port_names"] = list(r.port_names)
    (here / "run.json").write_text(json.dumps(meta, indent=1, default=str) + "\n", encoding="utf-8")
    print(f"step {r.run.steps}")
    print(json.dumps({"steps": r.run.steps, "stopped_by": r.run.stopped_by, "engine": r.run.engine}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
