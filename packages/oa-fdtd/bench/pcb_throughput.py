# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""B-PERF-01 PCB-like case: 400 x 400 x 120 graded grid, 6-pole ADE in 60 % of the cells, CPML on
all faces, about 1 % special (PEC / SIBC) edges.

``OA_RUN_GPU=1 python packages/oa-fdtd/bench/pcb_throughput.py [--steps 300] [--dtype float32]``
DEFERRED: do not run until the machine is confirmed stable (crashed the PC once); run at the gate.
Prints Gcells/s (cells x steps / wall time after a warm-up chunk). The dielectric fills the lower
72 of 120 z cells over the full footprint (60 % of the cells); a PEC ground plane and a SIBC
copper plane (1 % of the edges) sit at its bottom and top.
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np

from oa_fdtd.engine import CupyEngine
from oa_fdtd.grid import Grid
from oa_fdtd.model import Conductor, Model, Region, Waveform, compile_model


def graded(n: int, h0: float, ratio: float) -> np.ndarray:
    """``n + 1`` lines: cell size ``h0`` in the middle, growing by ``ratio`` towards both ends."""
    half = n // 2
    d = h0 * ratio ** np.arange(half)
    steps = np.concatenate([d[::-1], d])
    return np.concatenate([[0.0], np.cumsum(steps)])[: n + 1]


def build(nx: int = 400, ny: int = 400, nz: int = 120, poles: int = 6) -> Model:
    x = graded(nx, 0.1e-3, 1.004)
    y = graded(ny, 0.1e-3, 1.004)
    z = np.concatenate([[0.0], np.cumsum(np.full(nz, 0.05e-3))])
    g = Grid(x, y, z)
    big = 1.0
    z_die = float(z[72])
    pol = tuple((0.35 / poles, 1e-11 * 3.0**k) for k in range(poles))
    m = Model(g, Waveform("gauss_mod", 5e9, 8e9), np.array([5e9, 10e9]), walls=("cpml",) * 6)
    m.regions.append(Region((-big, -big, -big), (big, big, z_die), 3.9, 0.0, False, pol))
    z0 = float(z[10])
    m.regions.append(Region((-big, -big, z0 - 1e-6), (big, big, z0 + 1e-6), 1.0, 0.0, True))
    m.conductors.append(
        Conductor(
            (-big, -big, z_die), (big, big, z_die + 0.05e-3), 5.8e7, 35e-6, 0.0, None, "thick_sibc"
        )
    )
    return m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=300)
    ap.add_argument("--dtype", default="float32")
    a = ap.parse_args()
    model = build()
    c = compile_model(model, a.dtype)
    eng = CupyEngine(c, a.dtype, max_steps=a.steps + 512)
    eng.step(128)
    t0 = time.perf_counter()
    eng.step(a.steps)
    dt = time.perf_counter() - t0
    cells = int(np.prod(model.grid.n))
    print(
        json.dumps(
            {
                "cells": cells,
                "dtype": a.dtype,
                "gcells_s": cells * a.steps / dt / 1e9,
                "vram_gib": eng.peak_bytes() / 2**30,
                "ade_poles": 6,
            }
        )
    )


if __name__ == "__main__":
    main()
