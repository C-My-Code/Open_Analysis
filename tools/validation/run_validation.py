# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: E501
"""Single validation runner (WP-302).

    uv run python tools/validation/run_validation.py [--suites impedance,erc,dcir,ingest,em]
        [--quick] [--skip-openems] [--openems-root DIR] [--out docs/validation/results.json]
        [--page]

Writes one JSON results file (cases with source, reference class, expected, got, error and
status) and, with ``--page``, regenerates the static validation page from it. Exit status 1 when
any case fails (``known_gap`` cases are documented and do not fail the run).
Never starts WSL, Palace or a network connection.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from val_model import REPO, Case  # noqa: E402

SUITES = ("impedance", "erc", "dcir", "ingest", "em")


def _thread_caps() -> None:
    for var, val in (
        ("OMP_NUM_THREADS", "2"),
        ("OPENBLAS_NUM_THREADS", "2"),
        ("MKL_NUM_THREADS", "2"),
        ("OA_NUM_THREADS", "4"),  # openEMS engine threads; total CPU-heavy threads stay <= 8
    ):
        os.environ.setdefault(var, val)


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=REPO, capture_output=True, text=True, timeout=30, check=False
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def summarize(cases: list[dict]) -> list[dict]:  # type: ignore[type-arg]
    """Per category: counts by status; ``independent_passed`` counts independent references only."""
    cats: dict[str, Counter[str]] = {}
    for c in cases:
        k = cats.setdefault(c["category"], Counter())
        k["cases"] += 1
        k[c["status"]] += 1
        if c["status"] == "pass" and c["independent"]:
            k["independent_passed"] += 1
    rows = []
    for name in sorted(cats):
        k = cats[name]
        rows.append(
            {
                "category": name,
                "cases": k["cases"],
                "passed": k["pass"],
                "failed": k["fail"],
                "known_gap": k["known_gap"],
                "verify": k["verify"],
                "pending": k["pending"],
                "skipped": k["skipped"],
                "independent_passed": k["independent_passed"],
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--suites", default=",".join(SUITES))
    ap.add_argument("--quick", action="store_true", help="skip the 50-structure BEM-vs-FEM sweep")
    ap.add_argument("--skip-openems", action="store_true")
    ap.add_argument("--openems-root", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=REPO / "docs" / "validation" / "results.json")
    ap.add_argument("--page", action="store_true", help="also build the validation page")
    args = ap.parse_args(argv)
    _thread_caps()

    wanted = [s.strip() for s in args.suites.split(",") if s.strip()]
    unknown = [s for s in wanted if s not in SUITES]
    if unknown:
        ap.error(f"unknown suites {unknown}; known: {list(SUITES)}")

    def load(name: str) -> Callable[[], list[Case]]:
        if name == "impedance":
            import val_impedance

            return lambda: val_impedance.run(include_sweep=not args.quick)
        if name == "erc":
            import val_erc

            return val_erc.run
        if name == "dcir":
            import val_dcir

            return val_dcir.run
        if name == "ingest":
            import val_ingest

            return val_ingest.run
        import val_em

        return lambda: val_em.run(args.openems_root, with_openems=not args.skip_openems)

    t_start = time.perf_counter()
    all_cases: list[Case] = []
    suite_times: dict[str, float] = {}
    for name in wanted:
        t0 = time.perf_counter()
        print(f"[validation] {name} ...", flush=True)
        cases = load(name)()
        suite_times[name] = time.perf_counter() - t0
        bad = [c.id for c in cases if c.status == "fail"]
        print(
            f"[validation] {name}: {len(cases)} cases, {suite_times[name]:.1f} s, "
            f"{len(bad)} failed {bad[:5]}",
            flush=True,
        )
        all_cases += cases
    total = time.perf_counter() - t_start

    ids = [c.id for c in all_cases]
    dup = [i for i, n in Counter(ids).items() if n > 1]
    if dup:
        raise SystemExit(f"duplicate case ids: {dup[:5]}")
    rows = [c.to_json() for c in all_cases]
    status_counts = Counter(r["status"] for r in rows)
    doc = {
        "schema": "oa-validation-results/1",
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "git": {
            "commit": _git("rev-parse", "HEAD"),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        },
        "platform": f"{platform.system()} {platform.release()}, Python {platform.python_version()}",
        "runtime_s": round(total, 1),
        "suite_runtime_s": {k: round(v, 1) for k, v in suite_times.items()},
        "options": {"suites": wanted, "quick": args.quick, "skip_openems": args.skip_openems},
        "counting_rules": (
            "'passed' counts status 'pass' only. 'verify' (citation not checked), 'pending', "
            "'skipped' and 'known_gap' never count as passed. 'independent_passed' counts passes "
            "whose reference was not produced by this project's own code; self-computed references "
            "are labelled and never counted as independent validation."
        ),
        "summary": summarize(rows),
        "status_counts": dict(status_counts),
        "cases": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(doc, indent=1, allow_nan=False, default=str) + "\n", encoding="utf-8"
    )
    print(f"[validation] wrote {args.out} ({len(rows)} cases, {total:.0f} s)")
    for r in doc["summary"]:
        print(
            f"  {r['category']:<28} cases={r['cases']:<4} pass={r['passed']:<4} fail={r['failed']:<3} "
            f"known_gap={r['known_gap']:<3} verify={r['verify']:<3} pending={r['pending']:<3} "
            f"skipped={r['skipped']}"
        )
    if args.page:
        import build_page

        build_page.build(args.out, args.out.parent)
    failing = [r["id"] for r in rows if r["status"] == "fail"]
    if failing:
        print(f"[validation] FAILED: {failing}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
