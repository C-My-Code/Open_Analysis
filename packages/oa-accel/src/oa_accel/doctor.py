# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""``oa doctor``-style capability report: what GPU acceleration is available and why not."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from typing import Any

from oa_accel.memory import GIB, MemoryBudget
from oa_accel.policy import DevicePolicy, Thresholds, decide, resolve_spec
from oa_accel.probe import probe


def capabilities(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """JSON-ready capability report (the ``oa doctor`` accel section)."""
    e = os.environ if env is None else env
    pr = probe()
    th = Thresholds()
    devices = []
    for d in pr.devices:
        budget = MemoryBudget.from_env(d.total_bytes, env=e)
        devices.append(
            {
                "backend": d.kind,
                "index": d.index,
                "name": d.name,
                "arch": d.arch,
                "total_gib": round(d.total_bytes / GIB, 2),
                "free_gib": round(d.free_bytes / GIB, 2),
                "budget_gib": round(budget.gpu_bytes / GIB, 2),
                "supported_for_auto": d.supported,
                "untested_on_hardware": d.kind == "rocm",
            }
        )
    probe_big = decide(DevicePolicy(problem=_big(th)), pr, e)
    return {
        "cpu_fallback": True,
        "env": {
            k: e.get(k) for k in ("OA_DEVICE", "OA_GPU_MEM_GB", "OA_GPU_PINNED_GB", "OA_GPU_SLOTS")
        },
        "requested_device": resolve_spec(DevicePolicy(), e),
        "cupy": pr.cupy_version,
        "nvmath": pr.nvmath_version,
        "cudss": pr.cudss,
        "driver": pr.driver_version,
        "runtime": pr.runtime_version,
        "devices": devices,
        "unavailable_reason": pr.error,
        "auto_for_large_problem": {"backend": probe_big.backend, "reason": probe_big.reason},
        "crossover": {
            "dense_n": th.dense_n,
            "sparse_nnz": th.sparse_nnz,
            "fdtd_cells": th.fdtd_cells,
        },
    }


def _big(th: Thresholds) -> Any:
    from oa_accel.policy import Problem

    return Problem(kind="dense", n=th.dense_n * 4)


def format_text(cap: dict[str, Any]) -> str:
    lines = ["oa-accel capabilities", "  CPU fallback      : always available"]
    lines.append(f"  requested device  : {cap['requested_device']}")
    cupy_v = cap["cupy"] or "not installed"
    nvmath_v = cap["nvmath"] or "not installed"
    lines.append(f"  cupy / nvmath     : {cupy_v} / {nvmath_v}")
    lines.append(f"  cuDSS (sparse LU) : {'yes' if cap['cudss'] else 'no'}")
    lines.append(f"  driver / runtime  : {cap['driver'] or '-'} / {cap['runtime'] or '-'}")
    if not cap["devices"]:
        lines.append(f"  GPU devices       : none ({cap['unavailable_reason'] or 'n/a'})")
    for d in cap["devices"]:
        tag = " [untested on hardware]" if d["untested_on_hardware"] else ""
        lines.append(
            f"  GPU {d['backend']}:{d['index']}        : {d['name']} arch {d['arch']}, "
            f"{d['total_gib']} GiB total, {d['free_gib']} GiB free, budget {d['budget_gib']} GiB, "
            f"auto={'ok' if d['supported_for_auto'] else 'unsupported'}{tag}"
        )
    a = cap["auto_for_large_problem"]
    lines.append(f"  auto, large dense : {a['backend']} ({a['reason']})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    cap = capabilities()
    print(json.dumps(cap, indent=2) if "--json" in args else format_text(cap))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
