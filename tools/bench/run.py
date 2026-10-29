# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: E402
"""Benchmark runner (WP-304).

    uv run python tools/bench/run.py --list
    uv run python tools/bench/run.py --case B-ERC-01,B-IMP-01 [--device cpu|cuda|rocm]
    uv run python tools/bench/run.py --group impedance --profile bench --allow-heavy

Each runnable sub-case executes in its own worker process (cold run, then ``--reps`` warm runs;
the gated time is the warm median). The parent applies the run profile first (thread caps and
the memory-cap Job Object, from the root conftest code), so every worker inherits it. Heavy
sub-cases are only run with ``--allow-heavy`` (rule 11: the heavy/GPU token holder only);
without it they are reported ``ready``. Sub-cases whose solver or reference does not exist are
reported ``pending`` / ``ref-pending`` with the owning WP, never as passes.
"""

# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bench_model as bm
import bench_profile as bp

MARK = "OA_BENCH_RESULT "
DEVICES = ("cpu", "cuda", "rocm")


# ---------------------------------------------------------------------------------- worker
def _peak_rss_mb() -> float | None:
    try:
        import psutil

        mi = psutil.Process().memory_info()
        peak = getattr(mi, "peak_wset", None)  # Windows: process peak working set
        if peak:
            return float(peak) / 2**20
    except ImportError:
        pass
    try:
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0  # Linux: KiB
    except ImportError:
        return None


def _rss_mb() -> float | None:
    try:
        import psutil

        return float(psutil.Process().memory_info().rss) / 2**20
    except ImportError:
        return None


def _gpu_peak_mb() -> float | None:
    try:
        import cupy

        return float(cupy.get_default_memory_pool().total_bytes()) / 2**20
    except Exception:
        return None


def worker(case_id: str, sub_id: str, device: str, reps: int) -> int:
    import traceback

    import bench_cases

    out: dict[str, Any] = {"threads": int(os.environ.get("OA_NUM_THREADS", "0"))}
    try:
        case = bm.load_cases()[case_id]
        sub = next(s for s in case["sub"] if s["id"] == sub_id)
        fn = bench_cases.RUNNERS[sub["runner"]]
        out["rss_start_mb"] = _rss_mb()
        times: list[float] = []
        units = 1
        accuracy: float | None = None
        detail: dict[str, Any] = {}
        for _ in range(1 + reps):  # first is cold
            t0 = time.perf_counter()
            res = fn(device)
            wall = time.perf_counter() - t0
            times.append(float(res.get("time_s", wall)))
            units = int(res.get("units", 1))
            accuracy = res.get("accuracy", accuracy)
            detail = res.get("detail", detail)
        out |= {
            "accuracy": accuracy,
            "units": units,
            "time_cold_s": times[0],
            **bm.summarize_times(times[1:], units),
            "peak_rss_mb": _peak_rss_mb(),
            "gpu_peak_mb": _gpu_peak_mb() if device != "cpu" else None,
            "detail": detail,
        }
    except Exception as exc:  # reported as an ErrorInfo-style record, never a crash
        out["error"] = {
            "code": "bench.runner_failed",
            "message": f"{type(exc).__name__}: {exc}",
            "path": f"{case_id}/{sub_id}",
            "hint": traceback.format_exc(limit=4),
        }
    print(MARK + json.dumps(out, default=str))
    return 0


# ---------------------------------------------------------------------------------- parent
def _spawn(case_id: str, sub_id: str, device: str, reps: int, timeout_s: float) -> dict[str, Any]:
    cmd = [sys.executable, str(Path(__file__)), "--worker", case_id, sub_id, device, str(reps)]
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s, check=False, env=os.environ
        )
    except subprocess.TimeoutExpired:
        return {
            "error": {
                "code": "bench.timeout",
                "message": f"timeout {timeout_s:.0f} s",
                "path": f"{case_id}/{sub_id}",
                "hint": "raise --timeout",
            }
        }
    for line in p.stdout.splitlines():
        if line.startswith(MARK):
            return dict(json.loads(line[len(MARK) :]))
    return {
        "error": {
            "code": "bench.worker_crashed",
            "message": f"exit {p.returncode}",
            "path": f"{case_id}/{sub_id}",
            "hint": (p.stderr or p.stdout)[-600:],
        }
    }


def select(
    cases: dict[str, dict[str, Any]],
    case_ids: list[str] | None,
    groups: list[str] | None,
    subs: list[str] | None,
    device: str,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    sel = []
    for cid, case in cases.items():
        if case_ids and cid not in case_ids:
            continue
        if groups and case["group"] not in groups:
            continue
        for sub in case["sub"]:
            if subs and sub["id"] not in subs:
                continue
            if device in sub["devices"]:
                sel.append((case, sub))
    return sel


def run_entry(
    case: dict[str, Any],
    sub: dict[str, Any],
    device: str,
    *,
    runners: set[str],
    allow_heavy: bool,
    gpu_present: bool,
    reps: int,
    timeout_s: float,
) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "case": case["id"],
        "sub": sub["id"],
        "title": sub["title"],
        "group": case["group"],
        "device": device,
        "reference": case["reference"],
        "targets": sub["targets"][device],
        "owner": sub.get("owner", ""),
        "reason": sub.get("reason", ""),
    }
    pend = bm.declared_status(sub, runners)
    if pend:
        rec["status"] = pend
        rec["reason"] = rec["reason"] or "no runner registered yet"
    elif device != "cpu" and not gpu_present:
        rec["status"] = "skipped"
        rec["reason"] = "skipped (no device)"
    elif sub.get("heavy") and not allow_heavy:
        rec["status"] = "ready"
        rec["reason"] = "ready to run: heavy case, needs the heavy token and --allow-heavy"
    else:
        m = _spawn(case["id"], sub["id"], device, reps, timeout_s)
        rec["measured"] = m
        if "error" in m:
            rec["status"] = "fail"
            rec["reason"] = m["error"]["message"]
        else:
            rec["status"], rec["checks"] = bm.evaluate(rec["targets"], m)
    return rec


def build_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for r in records:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    by_case: dict[str, list[str]] = {}
    for r in records:
        by_case.setdefault(r["case"], []).append(r["status"])
    return {
        "status_counts": counts,
        "case_status": {k: bm.case_status(v) for k, v in sorted(by_case.items())},
    }


def result_path(out_dir: Path, fp: dict[str, Any], when: datetime) -> Path:
    return out_dir / f"{when:%Y-%m-%d}-{fp['commit'][:8] or 'nogit'}-{fp['host']}.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--list", action="store_true", help="list cases and sub-cases, then exit")
    ap.add_argument("--case", default="", help="comma-separated case ids (default: all)")
    ap.add_argument("--group", default="", help="comma-separated groups (default: all)")
    ap.add_argument("--sub", default="", help="comma-separated sub-case ids")
    ap.add_argument("--device", choices=DEVICES, default="cpu")
    ap.add_argument("--profile", choices=sorted(bp.PROFILES), default="bench")
    ap.add_argument("--reps", type=int, default=3, help="warm repetitions after the cold run")
    ap.add_argument(
        "--allow-heavy", action="store_true", help="run heavy sub-cases (needs the token)"
    )
    ap.add_argument("--skip-load-check", action="store_true", help="skip the CPU-load precheck")
    ap.add_argument("--timeout", type=float, default=900.0, help="per sub-case worker timeout [s]")
    ap.add_argument("--out-dir", type=Path, default=bm.RESULTS_DIR)
    ap.add_argument(
        "--check-regression",
        action="store_true",
        help="exit 1 on >20 %% slower or accuracy loss >25 %% of tolerance vs last pass",
    )
    ap.add_argument(
        "--worker", nargs=4, metavar=("CASE", "SUB", "DEVICE", "REPS"), help=argparse.SUPPRESS
    )
    a = ap.parse_args(argv)
    if a.worker:
        return worker(a.worker[0], a.worker[1], a.worker[2], int(a.worker[3]))

    import bench_cases

    cases = bm.load_cases()
    runners = set(bench_cases.RUNNERS)
    if a.list:
        for cid, c in cases.items():
            print(f"{cid}  [{c['group']}]  {c['title']}")
            for s in c["sub"]:
                st = bm.declared_status(s, runners) or ("ready" if s.get("heavy") else "runnable")
                print(f"    {s['id']:<24} {st:<12} {','.join(s['devices'])}  {s.get('owner', '')}")
        return 0

    split = lambda s: [x.strip() for x in s.split(",") if x.strip()] or None  # noqa: E731
    sel = select(cases, split(a.case), split(a.group), split(a.sub), a.device)
    unknown = [c for c in (split(a.case) or []) if c not in cases]
    if unknown:
        ap.error(f"unknown case ids {unknown}; known: {list(cases)}")
    if not sel:
        ap.error("no sub-case matches the selection and device")

    will_run = [
        1
        for _c, s in sel
        if not bm.declared_status(s, runners) and (a.allow_heavy or not s.get("heavy"))
    ]
    load: float | None = None
    load_note = "skipped"
    if will_run and a.profile == "bench" and not a.skip_load_check:
        load = bp.cpu_load_pct()
        load_note = "measured" if load is not None else "psutil missing"
        if load is not None and load > bp.CPU_LOAD_LIMIT_PCT:
            print(
                f"[bench] refusing to start: CPU load {load:.0f} % > {bp.CPU_LOAD_LIMIT_PCT:.0f} %"
                " (BENCHMARKS.md section 1); use --skip-load-check only if you know why"
            )
            return 2
    prof = bp.apply_profile(a.profile) if will_run else bp.PROFILES[a.profile]

    fp = bp.fingerprint()
    gpu_present = fp["gpu"] is not None
    when = datetime.now(UTC)
    records = []
    for case, sub in sel:
        rec = run_entry(
            case,
            sub,
            a.device,
            runners=runners,
            allow_heavy=a.allow_heavy,
            gpu_present=gpu_present,
            reps=a.reps,
            timeout_s=a.timeout,
        )
        records.append(rec)
        m = rec.get("measured", {})
        t = m.get("time_median_s")
        print(
            f"[bench] {rec['case']}/{rec['sub']} ({a.device}): {rec['status']}"
            + (
                f"  acc={m.get('accuracy')}  t={t:.4f}s  rss={m.get('peak_rss_mb', 0):.0f}MB"
                if t is not None
                else f"  {rec['reason']}"
            ),
            flush=True,
        )

    doc = {
        "schema": bm.SCHEMA,
        "generated": when.isoformat(timespec="seconds"),
        "profile": {
            "name": a.profile,
            **prof,
            "reps_warm": a.reps,
            "threads_env": os.environ.get("OA_NUM_THREADS"),
            "cpu_load_check": load_note,
            "cpu_load_pct": load,
            "allow_heavy": a.allow_heavy,
            "device": a.device,
        },
        "machine": fp,
        "accel_report": None,  # oa-accel does not exist yet (WP-503)
        "summary": build_summary(records),
        "results": records,
    }
    a.out_dir.mkdir(parents=True, exist_ok=True)
    path = result_path(a.out_dir, fp, when)
    if path.exists():  # merge: same day, commit and host; newer record per key wins
        old = json.loads(path.read_text(encoding="utf-8"))
        keep = {(r["case"], r["sub"], r["device"]): r for r in old["results"]}
        keep |= {(r["case"], r["sub"], r["device"]): r for r in records}
        doc["results"] = [keep[k] for k in sorted(keep)]
        doc["summary"] = build_summary(doc["results"])
    path.write_text(json.dumps(doc, indent=1, default=str) + "\n", encoding="utf-8")
    print(f"[bench] wrote {path}")

    if a.check_regression:
        import bench_report

        regs = bench_report.regressions(doc, bench_report.load_results(a.out_dir, exclude=path))
        for r in regs:
            print(f"[bench] REGRESSION {r}")
        if regs:
            return 1
    return 1 if any(r["status"] == "fail" for r in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())
