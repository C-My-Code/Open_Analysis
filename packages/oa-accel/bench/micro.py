# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Micro-benchmark: dense LU, sparse solve (cuDSS vs SuperLU, CG), SpMV. CPU vs GPU.

Run on the reference machine by the GPU token holder::

    OA_RUN_GPU=1 python packages/oa-accel/bench/micro.py [--json]

"GPU e2e" includes host-to-device and device-to-host copies; "GPU resident" excludes them
(inputs already on the device). Median of ``--reps`` warm runs after one warm-up. Sizes stay
below 2 GB of VRAM.
"""

# ruff: noqa: B023
# (the timing lambdas run inside the loop iteration that binds their variables)

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections.abc import Callable
from typing import Any

import numpy as np
import scipy.sparse as sp
from oa_accel.policy import DevicePolicy, select


def timeit(fn: Callable[[], Any], sync: Callable[[], None], reps: int) -> float:
    fn()
    sync()
    ts = []
    for _ in range(reps):
        t = time.perf_counter()
        fn()
        sync()
        ts.append(time.perf_counter() - t)
    return statistics.median(ts)


def grid(n: int) -> sp.csr_matrix:
    t = sp.diags([-1.0, 2.0, -1.0], [-1, 0, 1], shape=(n, n))
    i = sp.identity(n)
    return (sp.kron(t, i) + sp.kron(i, t) + 0.01 * sp.identity(n * n)).tocsr()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    cpu = select("cpu")
    gpu = select(DevicePolicy(device="cuda", fallback="error"))
    rows: list[dict[str, Any]] = []
    rng = np.random.default_rng(0)

    def add(name: str, size: str, c: float, e2e: float, res: float | None) -> None:
        rows.append({"op": name, "size": size, "cpu_s": c, "gpu_e2e_s": e2e, "gpu_resident_s": res})

    for n in (1000, 2000, 4000):
        a = rng.standard_normal((n, n)) + n**0.5 * np.eye(n)
        b = rng.standard_normal((n, 1))
        c = timeit(lambda: cpu.dense_solve(a, b), cpu.synchronize, args.reps)
        e = timeit(lambda: gpu.dense_solve(a, b), gpu.synchronize, args.reps)
        ad, bd = gpu.to_device(a), gpu.to_device(b)
        r = timeit(lambda: gpu.dense_solve(ad, bd, host=False), gpu.synchronize, args.reps)
        add("dense LU (getrf/getrs, f64)", f"n={n}", c, e, r)

    for side in (300, 700, 1000):  # 9e4, 4.9e5, 1e6 unknowns
        a = grid(side)
        b = np.ones((a.shape[0], 1))
        label = f"n={a.shape[0]:.2e} nnz={a.nnz:.2e}"
        ad, bd = gpu.to_device(a), gpu.to_device(b)
        c = timeit(lambda: cpu.sparse_solve(a, b, "direct"), cpu.synchronize, args.reps)
        e = timeit(
            lambda: gpu.sparse_solve(a, b, "direct", assume="spd"), gpu.synchronize, args.reps
        )
        r = timeit(
            lambda: gpu.sparse_solve(ad, bd, "direct", assume="spd", host=False),
            gpu.synchronize,
            args.reps,
        )
        add("sparse direct (SuperLU vs cuDSS)", label, c, e, r)
        if side <= 700:  # CG-Jacobi needs thousands of iterations on big grids; keep it short
            c = timeit(
                lambda: cpu.sparse_solve(a, b, "cg", "jacobi", 1e-8, assume="spd"),
                cpu.synchronize,
                1,
            )
            e = timeit(
                lambda: gpu.sparse_solve(a, b, "cg", "jacobi", 1e-8, assume="spd"),
                gpu.synchronize,
                1,
            )
            add("sparse CG+Jacobi rtol 1e-8", label, c, e, None)
        x = rng.standard_normal(a.shape[0])
        xd = gpu.to_device(x)
        c = timeit(lambda: cpu.sparse_matvec(a, x), cpu.synchronize, 20)
        e = timeit(lambda: gpu.sparse_matvec(a, x), gpu.synchronize, 20)
        r = timeit(lambda: gpu.sparse_matvec(ad, xd, host=False), gpu.synchronize, 20)
        add("SpMV", label, c, e, r)

    rep = gpu.report()
    if args.json:
        print(json.dumps({"device": rep.to_dict(), "rows": rows}, indent=2))
        return
    print(
        f"GPU: {rep.device_name} (cc {rep.compute_capability}), cupy {rep.library_versions['cupy']}"
    )
    print("| op | size | CPU [ms] | GPU e2e [ms] | GPU resident [ms] | speedup e2e |")
    print("|---|---|---|---|---|---|")
    for row in rows:
        res = "-" if row["gpu_resident_s"] is None else f"{row['gpu_resident_s'] * 1e3:.2f}"
        cpu_ms, e2e_ms = row["cpu_s"] * 1e3, row["gpu_e2e_s"] * 1e3
        print(
            f"| {row['op']} | {row['size']} | {cpu_ms:.2f} | {e2e_ms:.2f} | {res} | "
            f"{cpu_ms / e2e_ms:.1f}x |"
        )
    print(f"peak GPU pool: {rep.peak_bytes / 2**20:.0f} MiB; fallbacks: {rep.fallbacks}")


if __name__ == "__main__":
    main()
