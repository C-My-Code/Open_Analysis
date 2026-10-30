# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""B-PERF-01 plain-kernel throughput: 256^3 vacuum grid, 10-cell CPML on all faces.

``OA_RUN_GPU=1 python packages/oa-fdtd/bench/throughput.py [--n 256] [--steps 200] [--json]``
Reports Gcells/s (cells x steps / wall time) for float32 and float64; the NumPy engine is
reported for information on a small grid.
"""

from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np
from oa_fdtd.grid import Grid
from oa_fdtd.model import Model, Waveform, compile_model


def build(n: int) -> Model:
    h = 1e-3
    lines = np.arange(n + 1) * h
    return Model(
        Grid(lines, lines, lines),
        Waveform("gauss_mod", 5e9, 5e9),
        np.array([5e9]),
        walls=("cpml",) * 6,
    )


def gpu_rate(n: int, steps: int, dtype: str, graph: bool) -> float:
    from oa_fdtd.engine import CupyEngine

    eng = CupyEngine(compile_model(build(n), dtype), dtype, use_graph=graph, max_steps=steps + 256)
    eng.step(128)  # warm-up: NVRTC compile, graph capture
    t0 = time.perf_counter()
    eng.step(steps)
    dt = time.perf_counter() - t0
    return n**3 * steps / dt / 1e9


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=256)
    ap.add_argument("--steps", type=int, default=192)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    out: dict[str, float] = {}
    if os.environ.get("OA_RUN_GPU") == "1":
        for dtype in ("float32", "float64"):
            out[f"{dtype}_gcells_s"] = gpu_rate(a.n, a.steps, dtype, True)
        out["float32_nograph_gcells_s"] = gpu_rate(a.n, a.steps, "float32", False)
    from oa_fdtd.reference import NumpyEngine

    n_small = 48
    eng = NumpyEngine(compile_model(build(n_small), "float64"), "float64")
    t0 = time.perf_counter()
    eng.step(20)
    out["numpy_float64_gcells_s"] = n_small**3 * 20 / (time.perf_counter() - t0) / 1e9
    print(json.dumps(out, indent=2) if a.json else out)


if __name__ == "__main__":
    main()
