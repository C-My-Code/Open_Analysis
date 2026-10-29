# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: E501
"""Build the published validation page from ``results.json`` (WP-302).

    uv run python tools/validation/build_page.py [--results docs/validation/results.json]
        [--out docs/validation]

Writes ``index.md``, one ``<area>.md`` per area (tables of case, source, class, expected, got,
error, tolerance and status), ``plots/<area>.svg`` (error as a fraction of the tolerance, one dot
per case) and a self-contained ``validation.html``. Pure standard library; deterministic apart
from the timestamp stored in the results file.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
AREAS = {
    "impedance": ("Impedance (closed form, BEM, FEM)", "impedance"),
    "dc-ir": ("DC IR drop", "dc-ir"),
    "erc": ("ERC", "erc"),
    "ingest": ("Ingest", "ingest"),
    "em": ("EM (openEMS, Palace slots)", "em"),
}
STATUS_LABEL = {
    "pass": "pass",
    "fail": "FAIL",
    "known_gap": "known gap",
    "verify": "[verify]",
    "pending": "pending",
    "skipped": "skipped",
}
COLORS = {
    "pass": "#2a7f3f",
    "fail": "#c0392b",
    "known_gap": "#d68910",
    "verify": "#7f8c8d",
    "pending": "#7f8c8d",
    "skipped": "#7f8c8d",
}


def _area(cat: str) -> str:
    return cat.split("/")[0]


def _num(v: float | None, digits: int = 5) -> str:
    if v is None:
        return "-"
    if v == 0:
        return "0"
    if abs(v) >= 1e4 or abs(v) < 1e-3:
        return f"{v:.4e}"
    return f"{v:.{digits}g}"


def _err(c: dict) -> str:  # type: ignore[type-arg]
    if c["error_pct"] is not None:
        return f"{c['error_pct']:+.4f} %"
    if c["error_abs"] is not None:
        return f"{c['error_abs']:+.4g}"
    return "-"


def _fraction(c: dict) -> float | None:  # type: ignore[type-arg]
    """|error| as a fraction of the tolerance (1.0 = at the limit); None when not meaningful."""
    t = c["tolerance"]
    if c["expected"] is None or c["got"] is None:
        return None
    if t["kind"] == "rel" and c["expected"] != 0:
        return abs(c["got"] - c["expected"]) / (t["value"] * abs(c["expected"]))
    if t["kind"] == "abs" and t["value"] > 0:
        return abs(c["got"] - c["expected"]) / t["value"]
    return None


def plot_svg(cases: list[dict]) -> str:  # type: ignore[type-arg]
    """One row per sub-category and reference class; one dot per case at |err| / tolerance."""
    rows: dict[str, list[dict]] = {}  # type: ignore[type-arg]
    for c in cases:
        f = _fraction(c)
        if f is None or c["status"] in ("verify", "pending", "skipped"):
            continue
        rows.setdefault(f"{c['category']} [{c['reference_class']}]", []).append({**c, "_f": f})
    if not rows:
        return ""
    xmax = 1.5
    left, width, rowh = 330, 560, 26
    height = 40 + rowh * len(rows)
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {left + width + 30} {height}" '
        'role="img" aria-label="Error as a fraction of the tolerance, one dot per case" '
        'font-family="sans-serif" font-size="11">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
    ]
    x1 = left + width / xmax
    out.append(
        f'<line x1="{x1:.1f}" y1="14" x2="{x1:.1f}" y2="{height - 22}" stroke="#c0392b" '
        'stroke-dasharray="4 3"/>'
    )
    out.append(f'<text x="{x1:.1f}" y="10" text-anchor="middle" fill="#c0392b">tolerance</text>')
    for k, (label, items) in enumerate(sorted(rows.items())):
        y = 26 + k * rowh
        out.append(
            f'<text x="{left - 8}" y="{y + 4}" text-anchor="end" fill="#222">{html.escape(label)} ({len(items)})</text>'
        )
        out.append(f'<line x1="{left}" y1="{y}" x2="{left + width}" y2="{y}" stroke="#ddd"/>')
        for i, c in enumerate(items):
            x = left + min(c["_f"], xmax) / xmax * width
            jitter = ((i * 7) % 9 - 4) * 1.4
            out.append(
                f'<circle cx="{x:.1f}" cy="{y + jitter:.1f}" r="3.2" fill="{COLORS[c["status"]]}" '
                f'fill-opacity="0.8"><title>{html.escape(c["id"])}: {c["_f"]:.3g} x tolerance</title></circle>'
            )
    ax = height - 12
    for v in (0.0, 0.5, 1.0, 1.5):
        x = left + v / xmax * width
        out.append(f'<text x="{x:.1f}" y="{ax}" text-anchor="middle" fill="#555">{v:g}</text>')
    out.append(
        f'<text x="{left + width / 2}" y="{height - 1}" text-anchor="middle" fill="#555">'
        "|error| / tolerance (green pass, red fail, amber known gap)</text></svg>"
    )
    return "\n".join(out)


def _md_cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def table_md(cases: list[dict]) -> str:  # type: ignore[type-arg]
    lines = [
        "| Case | Source | Class | Indep. | Quantity | Expected | Got | Error | Tolerance | Status |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in cases:
        src = c["source"]
        src = src if len(src) <= 160 else src[:157] + "..."
        d = f"`{c['id']}`" + (f"<br>`{c['case_dir']}`" if c["case_dir"] else "")
        unit = f" {c['unit']}" if c["unit"] else ""
        lines.append(
            "| "
            + " | ".join(
                _md_cell(x)
                for x in (
                    f"{d}<br>{c['title']}",
                    src,
                    c["reference_class"],
                    "yes" if c["independent"] else "no",
                    c["quantity"],
                    _num(c["expected"]) + unit if c["expected"] is not None else "-",
                    _num(c["got"]) + unit if c["got"] is not None else "-",
                    _err(c),
                    c["tolerance"]["text"],
                    STATUS_LABEL[c["status"]],
                )
            )
            + " |"
        )
    return "\n".join(lines)


def summary_md(rows: list[dict]) -> str:  # type: ignore[type-arg]
    lines = [
        "| Category | Cases | Passed | Failed | Known gap | [verify] | Pending | Skipped | Passed with an independent reference |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    tot = dict.fromkeys(
        (
            "cases",
            "passed",
            "failed",
            "known_gap",
            "verify",
            "pending",
            "skipped",
            "independent_passed",
        ),
        0,
    )
    for r in rows:
        lines.append(
            f"| {r['category']} | {r['cases']} | {r['passed']} | {r['failed']} | {r['known_gap']} | "
            f"{r['verify']} | {r['pending']} | {r['skipped']} | {r['independent_passed']} |"
        )
        for k in tot:
            tot[k] += r[k]
    lines.append(
        f"| **Total** | **{tot['cases']}** | **{tot['passed']}** | **{tot['failed']}** | "
        f"**{tot['known_gap']}** | **{tot['verify']}** | **{tot['pending']}** | **{tot['skipped']}** | "
        f"**{tot['independent_passed']}** |"
    )
    return "\n".join(lines)


def build(results: Path, out: Path) -> None:
    doc = json.loads(results.read_text(encoding="utf-8"))
    cases: list[dict] = doc["cases"]  # type: ignore[type-arg]
    out.mkdir(parents=True, exist_ok=True)
    (out / "plots").mkdir(exist_ok=True)
    by_area: dict[str, list[dict]] = {}  # type: ignore[type-arg]
    for c in cases:
        by_area.setdefault(_area(c["category"]), []).append(c)

    failing = [c for c in cases if c["status"] == "fail"]
    meta = (
        f"Generated {doc['generated']} from commit `{doc['git']['commit'][:12]}` "
        f"({doc['git']['branch']}) on {doc['platform']}; full run {doc['runtime_s']:.0f} s."
    )
    index = [
        "# Validation",
        "",
        "Every number on these pages is produced by `tools/validation/run_validation.py` and stored in "
        "[`results.json`](results.json); the pages are generated by `tools/validation/build_page.py`. "
        "Nothing here is typed in by hand.",
        "",
        meta,
        "",
        "## Summary",
        "",
        summary_md(doc["summary"]),
        "",
        "## How to read this",
        "",
        "- **Reference class.** *analytic*: an exact formula or a construction with a known answer. "
        "*published*: a value from a cited paper, book or standard. *cross-solver*: another solver or "
        "reader of the same problem. *measured*: a physical measurement (none yet). "
        "*self-computed*: the reference was produced by this project's own code (regression locks, "
        "report values never recomputed independently). Self-computed cases are shown for "
        "regression only and are **never counted as independent validation**; the last column of the "
        "table counts passes with an independent reference only.",
        "- **Status.** *pass* and *FAIL* are against the tolerance stated in the row. *known gap* is a "
        "documented shortfall (see `docs/FOLLOWUPS.md`) and does not count as passed. *[verify]* means "
        "the citation could not be checked; the case is shown but excluded from every pass count. "
        "*pending* and *skipped* cases were not run (for example Palace: pending WP-301).",
        "- **Independent.** `no` means the reference shares code, or a network model, with the "
        "implementation under test.",
        "",
        "## Areas",
        "",
    ]
    for key, (title, _slug) in AREAS.items():
        n = len(by_area.get(key, []))
        index.append(f"- [{title}]({key}.md): {n} cases")
    index += [
        "- [ROADMAP M1 gate map](gates.md): which case covers which gate, and what is not covered",
        "",
        "## Failing cases",
        "",
    ]
    if failing:
        index += [
            f"- `{c['id']}`: {c['title']} (error {_err(c)}, tolerance {c['tolerance']['text']})"
            for c in failing
        ]
    else:
        index.append("None in this run.")
    index += [
        "",
        "## Known limits of this page",
        "",
        "- Published-reference tables (Polar SI9000 app notes, Wadell worked examples, Bryant-Weiss, "
        "TNT-MMTL/atlc) are listed as `[verify]` slots: no value was typed from memory without a "
        "checkable source.",
        "- Palace is pending WP-301; no WSL or Palace run is part of this page.",
        "- The openEMS tutorials (MSL notch, patch) are not reproduced yet.",
        "- No measured (VNA) data exists yet.",
        "",
    ]
    (out / "index.md").write_text("\n".join(index), encoding="utf-8")

    html_parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Open_Analysis validation</title><style>"
        "body{font-family:sans-serif;margin:1rem auto;max-width:1200px;padding:0 1rem;color:#222}"
        "table{border-collapse:collapse;font-size:12px;width:100%}"
        "td,th{border:1px solid #ccc;padding:3px 5px;vertical-align:top;text-align:left}"
        "th{background:#f0f0f0}.fail{color:#c0392b;font-weight:bold}.pass{color:#2a7f3f}"
        ".known_gap{color:#d68910}.verify,.pending,.skipped{color:#7f8c8d}"
        "svg{max-width:100%;height:auto}</style></head><body>"
        f"<h1>Open_Analysis validation</h1><p>{html.escape(meta)}</p>"
    ]
    html_parts.append(
        "<h2>Summary</h2><table><tr>"
        + "".join(
            f"<th>{h}</th>"
            for h in (
                "Category",
                "Cases",
                "Passed",
                "Failed",
                "Known gap",
                "[verify]",
                "Pending",
                "Skipped",
                "Independent passes",
            )
        )
        + "</tr>"
    )
    for r in doc["summary"]:
        html_parts.append(
            "<tr>"
            + "".join(
                f"<td>{html.escape(str(r[k]))}</td>"
                for k in (
                    "category",
                    "cases",
                    "passed",
                    "failed",
                    "known_gap",
                    "verify",
                    "pending",
                    "skipped",
                    "independent_passed",
                )
            )
            + "</tr>"
        )
    html_parts.append("</table>")

    for key, (title, _slug) in AREAS.items():
        items = by_area.get(key, [])
        svg = plot_svg(items)
        if svg:
            (out / "plots" / f"{key}.svg").write_text(svg + "\n", encoding="utf-8")
        page = [f"# {title}", "", f"[Back to the summary](index.md). {meta}", ""]
        if svg:
            page += [f"![Error as a fraction of the tolerance](plots/{key}.svg)", ""]
        subs: dict[str, list[dict]] = {}  # type: ignore[type-arg]
        for c in items:
            subs.setdefault(c["category"], []).append(c)
        html_parts.append(f"<h2>{html.escape(title)}</h2>{svg}")
        for sub in sorted(subs):
            page += [f"## {sub}", "", table_md(subs[sub]), ""]
            html_parts.append(
                f"<h3>{html.escape(sub)}</h3><table><tr>"
                + "".join(
                    f"<th>{h}</th>"
                    for h in (
                        "Case",
                        "Source",
                        "Class",
                        "Indep.",
                        "Expected",
                        "Got",
                        "Error",
                        "Tolerance",
                        "Status",
                    )
                )
                + "</tr>"
            )
            for c in subs[sub]:
                unit = f" {c['unit']}" if c["unit"] else ""
                html_parts.append(
                    "<tr>"
                    f"<td><code>{html.escape(c['id'])}</code><br>{html.escape(c['title'])}</td>"
                    f"<td>{html.escape(c['source'][:300])}</td><td>{c['reference_class']}</td>"
                    f"<td>{'yes' if c['independent'] else 'no'}</td>"
                    f"<td>{_num(c['expected'])}{unit if c['expected'] is not None else ''}</td>"
                    f"<td>{_num(c['got'])}{unit if c['got'] is not None else ''}</td>"
                    f"<td>{html.escape(_err(c))}</td><td>{html.escape(c['tolerance']['text'])}</td>"
                    f"<td class='{c['status']}'>{STATUS_LABEL[c['status']]}</td></tr>"
                )
            html_parts.append("</table>")
        (out / f"{key}.md").write_text("\n".join(page), encoding="utf-8")
    html_parts.append("</body></html>")
    (out / "validation.html").write_text("\n".join(html_parts) + "\n", encoding="utf-8")
    print(f"[validation] page written to {out} ({len(cases)} cases, {len(failing)} failing)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", type=Path, default=REPO / "docs" / "validation" / "results.json")
    ap.add_argument("--out", type=Path, default=REPO / "docs" / "validation")
    a = ap.parse_args()
    build(a.results, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
