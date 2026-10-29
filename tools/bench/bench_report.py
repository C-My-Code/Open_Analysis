# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: E501
"""Benchmark pages and regression check from committed result files (WP-304).

``build(results_dir, out_dir)`` writes ``index.md`` and one page per group under ``out_dir``
(``docs/validation/benchmarks/``), plus history plots. ``performance_section`` is the
"Performance" section that ``tools/validation/build_page.py`` adds to the validation page.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

import bench_model as bm

SLOWER = 1.20  # more than 20 % slower than the last passing run fails the nightly job
ACC_LOSS = 0.25  # accuracy loss of more than 25 % of the tolerance fails it

Key = tuple[str, str, str]  # case, sub, device


def load_results(results_dir: Path, exclude: Path | None = None) -> list[dict[str, Any]]:
    """All ``oa-bench/1`` files, oldest first (file names start with the date)."""
    docs = []
    for f in sorted(results_dir.glob("*.json")):
        if exclude is not None and f.resolve() == exclude.resolve():
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("schema") == bm.SCHEMA:
            d["_file"] = f.name
            docs.append(d)
    return docs


def latest(docs: list[dict[str, Any]]) -> dict[Key, tuple[dict[str, Any], dict[str, Any]]]:
    """Newest record per (case, sub, device); measured records win over declared ones."""
    out: dict[Key, tuple[dict[str, Any], dict[str, Any]]] = {}
    for d in docs:
        for r in d["results"]:
            k = (r["case"], r["sub"], r["device"])
            old = out.get(k)
            if old is None or "measured" in r or "measured" not in old[1]:
                out[k] = (d, r)
    return out


def regressions(new: dict[str, Any], old_docs: list[dict[str, Any]]) -> list[str]:
    """Compare ``new`` with the most recent passing record of each key in ``old_docs``."""
    last_pass: dict[Key, dict[str, Any]] = {}
    for d in old_docs:
        for r in d["results"]:
            if r["status"] == "pass" and "measured" in r:
                last_pass[(r["case"], r["sub"], r["device"])] = r
    out = []
    for r in new["results"]:
        k = (r["case"], r["sub"], r["device"])
        base = last_pass.get(k)
        if base is None or "measured" not in r or "error" in r["measured"]:
            continue
        n, o = r["measured"], base["measured"]
        nt, ot = n.get("time_median_s"), o.get("time_median_s")
        if nt is not None and ot and nt > SLOWER * ot:
            out.append(f"{'/'.join(k)}: time {nt:.4g} s vs {ot:.4g} s ({nt / ot - 1:+.0%})")
        tol = r["targets"].get("accuracy_max")
        na, oa = n.get("accuracy"), o.get("accuracy")
        if tol and na is not None and oa is not None and na - oa > ACC_LOSS * tol:
            out.append(f"{'/'.join(k)}: accuracy {na:.4g} vs {oa:.4g} (tolerance {tol:g})")
    return out


# ---------------------------------------------------------------------------------- pages
def _declared(sub: dict[str, Any]) -> str:
    """Status of a sub-case that no result file mentions yet."""
    return str(sub.get("pending") or ("ready" if sub.get("runner") else "pending"))


def _f(v: float | None, digits: int = 4) -> str:
    return "-" if v is None else f"{v:.{digits}g}"


def _target_text(t: dict[str, Any]) -> str:
    parts = []
    if "accuracy_max" in t:
        parts.append(f"acc <= {t['accuracy_max']:g} ({t.get('accuracy_unit', '')})")
    if "time_max_s" in t:
        parts.append(f"time <= {t['time_max_s']:g} s per {t.get('time_per', 'run')}")
    if "mem_max_mb" in t:
        parts.append(f"mem <= {t['mem_max_mb']:g} MB")
    if "gpu_mem_max_mb" in t:
        parts.append(f"GPU <= {t['gpu_mem_max_mb']:g} MB")
    return "; ".join(parts) or "not gated"


def _cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def history_svg(points: list[tuple[str, float]], limit: float | None, label: str) -> str:
    """A small time-per-commit polyline (inline SVG, no external assets)."""
    w, h, pad = 360, 90, 8
    ys = [p[1] for p in points] + ([limit] if limit else [])
    lo, hi = min(ys), max(ys)
    span = (hi - lo) or 1.0

    def xy(i: int, v: float) -> str:
        x = pad + (w - 2 * pad) * (i / max(len(points) - 1, 1))
        return f"{x:.1f},{h - pad - (h - 2 * pad) * (v - lo) / span:.1f}"

    line = " ".join(xy(i, v) for i, (_c, v) in enumerate(points))
    lim = ""
    if limit:
        y = xy(0, limit).split(",")[1]
        lim = f'<line x1="{pad}" x2="{w - pad}" y1="{y}" y2="{y}" stroke="#c0392b" stroke-dasharray="4"/>'
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f"<title>{html.escape(label)}</title>{lim}"
        f'<polyline fill="none" stroke="#2a6f97" stroke-width="2" points="{line}"/></svg>'
    )


def _rows(items: list[tuple[Key, dict[str, Any], dict[str, Any]]]) -> list[str]:
    lines = [
        "| Case | Sub-case | Device | Status | Target | Measured accuracy | Median warm | Cold | "
        "Peak host MB | Peak GPU MB | Threads | Owner / note |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for (cid, sid, dev), _d, r in items:
        m = r.get("measured", {})
        note = r.get("owner", "")
        if r["status"] not in ("pass", "fail") and r.get("reason"):
            note = f"{note}: {r['reason']}" if note else r["reason"]
        if r["status"] == "fail":
            bad = [c["name"] for c in r.get("checks", []) if not c["ok"]]
            note = "over: " + ", ".join(bad) if bad else r.get("reason", "")
        lines.append(
            f"| [{cid}](index.md#{cid.lower()}) | {sid} | {dev} | **{r['status']}** | "
            f"{_cell(_target_text(r['targets']))} | {_f(m.get('accuracy'))} | "
            f"{_f(m.get('time_median_s'))} s | {_f(m.get('time_cold_s'))} s | "
            f"{_f(m.get('peak_rss_mb'), 4)} | {_f(m.get('gpu_peak_mb'), 4)} | "
            f"{m.get('threads', '-')} | {_cell(note)} |"
        )
    return lines


def _history(docs: list[dict[str, Any]], key: Key) -> list[tuple[str, float]]:
    pts = []
    for d in docs:
        for r in d["results"]:
            if (r["case"], r["sub"], r["device"]) == key and "measured" in r:
                t = (
                    r["measured"].get("time_per_unit_s")
                    if r["targets"].get("time_per", "run") != "run"
                    else r["measured"].get("time_median_s")
                )
                if t is not None:
                    pts.append((d["machine"]["commit"][:8], float(t)))
    return pts


def summary_table(docs: list[dict[str, Any]]) -> str:
    lat = latest(docs)
    cases = bm.load_cases()
    st: dict[str, list[str]] = {}
    for (cid, _s, _d), (_doc, r) in lat.items():
        st.setdefault(cid, []).append(r["status"])
    for cid, c in cases.items():  # declared but never recorded: still listed
        if cid not in st:
            st[cid] = [_declared(s) for s in c["sub"]]
    lines = ["| Case | Title | Status | Sub-cases |", "|---|---|---|---|"]
    for cid in sorted(st):
        n = len(st[cid])
        npass = st[cid].count("pass")
        lines.append(
            f"| [{cid}](index.md#{cid.lower()}) | {cases[cid]['title'] if cid in cases else ''} | "
            f"**{bm.case_status(st[cid])}** | {npass} of {n} pass |"
        )
    return "\n".join(lines)


def performance_section(results_dir: Path, link: str = "benchmarks/index.md") -> list[str]:
    """Markdown lines for the "Performance" section of the validation page."""
    docs = load_results(results_dir) if results_dir.is_dir() else []
    out = [
        "## Performance",
        "",
        "Benchmark gates of [docs/BENCHMARKS.md](../BENCHMARKS.md), run by `tools/bench/run.py` "
        f"(schema `oa-bench/1`). Details, targets per backend and history: [benchmarks]({link}). "
        "`pass` needs accuracy, runtime and memory inside the targets; `pending`, `ref-pending`, "
        "`verify`, `ready` and `skipped` never count as passes.",
        "",
    ]
    if not docs:
        return [*out, "No benchmark results committed yet.", ""]
    out += [summary_table(docs), ""]
    return out


def build(results_dir: Path, out_dir: Path) -> None:
    docs = load_results(results_dir) if results_dir.is_dir() else []
    cases = bm.load_cases()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "plots").mkdir(exist_ok=True)
    lat = latest(docs)
    # declared-only entries for sub-cases never recorded, so every case id appears
    recorded = {(k[0], k[1]) for k in lat}
    groups: dict[str, list[tuple[Key, dict[str, Any], dict[str, Any]]]] = {}
    for key in sorted(lat):
        d, r = lat[key]
        groups.setdefault(r["group"], []).append((key, d, r))
    for cid, c in cases.items():
        for s in c["sub"]:
            if (cid, s["id"]) not in recorded:
                dev = s["devices"][0]
                r = {
                    "status": _declared(s),
                    "targets": s["targets"][dev],
                    "owner": s.get("owner", ""),
                    "reason": s.get("reason", "not recorded yet"),
                    "group": c["group"],
                    "case": cid,
                    "sub": s["id"],
                    "device": dev,
                }
                groups.setdefault(c["group"], []).append(((cid, s["id"], dev), {}, r))
    meta = ""
    if docs:
        d = docs[-1]
        m = d["machine"]
        meta = (
            f"Latest file `{d['_file']}`: {m['cpu']}, {m['logical_cores']} logical cores, "
            f"{m['ram_gb']} GB, GPU {(m['gpu'] or {}).get('name', 'none')}; profile "
            f"`{d['profile']['name']}` ({d['profile']['threads_env']} threads, "
            f"{d['profile']['mem_gb']:g} GB cap); CPU-load check {d['profile']['cpu_load_check']}."
        )
    index = [
        "# Benchmarks",
        "",
        "Gates and protocol: [BENCHMARKS.md](../../BENCHMARKS.md). Every row is produced by "
        "`tools/bench/run.py` from the committed files in `tests/benchmarks/results/`; nothing is "
        "typed in by hand. Times are the median of warm runs; the cold run is shown separately.",
        "",
        meta,
        "",
        "## Cases",
        "",
        summary_table(docs),
        "",
        "## Groups",
        "",
        *[f"- [{g}]({g}.md): {len(v)} sub-cases" for g, v in sorted(groups.items())],
        "",
        "## Case references",
        "",
    ]
    for cid, c in cases.items():
        ref = c["reference"]
        index += [
            f"### {cid}",
            "",
            f"{c['title']}. Spec: [{c['spec']}](../../../{c['spec'].split('#')[0]}).",
            "",
            f"- Targets (verbatim from BENCHMARKS.md section 2): {c['targets_text']}",
            f"- Reference class: {ref['class']}. {ref['citation']}",
            f"- Reference data: `{ref['data'] or 'none'}`",
            f"- Case file: `tests/benchmarks/cases/{cid}/case.toml`",
            "",
        ]
    (out_dir / "index.md").write_text("\n".join(index), encoding="utf-8")
    for g, items in sorted(groups.items()):
        page = [f"# Benchmarks: {g}", "", "[Back to benchmarks](index.md)", "", *_rows(items), ""]
        for key, _d, r in items:
            pts = _history(docs, key)
            if len(pts) >= 2:
                name = "-".join(key) + ".svg"
                lim = r["targets"].get("time_max_s")
                (out_dir / "plots" / name).write_text(
                    history_svg(pts, lim, "/".join(key)), encoding="utf-8"
                )
                page += [
                    f"History {'/'.join(key)} (time per commit, dashed = target):",
                    "",
                    f"![{'/'.join(key)}](plots/{name})",
                    "",
                ]
        (out_dir / f"{g}.md").write_text("\n".join(page), encoding="utf-8")
