# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ingest fidelity report (WP-208): how faithfully was this board read?

For each board the report gives counts, ingest diagnostics, zone freshness, and the comparison
of the native KiCad reader with the IPC-2581 reader and with ``expected.yaml`` when they exist.
It is written for agents (JSON, schema ``oa-ingest-report/1``) and for people (``--text``).

    uv run python tests/integration/ingest/ingest_report.py --synthetic --text
    uv run python tests/integration/ingest/ingest_report.py board.kicad_pcb --json report.json
    uv run python tests/integration/ingest/ingest_report.py --demos --csv diff.csv --json -

A path is a ``.kicad_pcb`` file or a directory holding ``board.kicad_pcb`` (WP-102 layout: the
sibling ``board.ipc2581.xml`` and ``expected.yaml`` are used when present). ``--ipc auto`` (the
default) exports the IPC-2581 file with ``kicad-cli`` when no sibling file exists and KiCad is
installed. Exit status: 0 when no finding failed, 1 otherwise, 2 for usage or read errors.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ingest_xval as xv

SCHEMA = "oa-ingest-report/1"


def _diag_counts(board: Any) -> dict[str, int]:
    c = Counter(d.code for d in board.diagnostics)
    return dict(sorted(c.items()))


def board_summary(board: Any, areas: xv.AreaCache | None) -> dict[str, Any]:
    """Counts and fidelity-relevant facts of one ``Board``."""
    kinds = Counter(v.kind for v in board.vias)
    zones = [z for z in board.zones if z.keepout is None]
    stale = [{"zone": z, "area_outside_m2": a} for z, a in xv.fill_outside_outline(board)]
    out: dict[str, Any] = {
        "source_tool": board.meta.source_tool,
        "source_version": board.meta.source_version,
        "counts": {
            "nets": len(board.nets),
            "components": len(board.components),
            "pads": len(board.pads),
            "tracks": len(board.tracks),
            "vias": dict(sorted(kinds.items())),
            "backdrilled_vias": sum(1 for v in board.vias if v.backdrills),
            "zones": len(zones),
            "rule_areas": len(board.zones) - len(zones),
            "copper_layers": len(xv.conductor_layers(board)),
        },
        "stackup": {
            "known": board.stackup.known,
            "total_thickness_m": board.stackup.total_thickness,
            "layers": len(board.stackup.layers),
        },
        "outline_area_m2": xv._poly(board.outline.polygon).area,
        "zones": {
            "fill_state": dict(sorted(Counter(z.fill_state for z in zones).items())),
            "fill_outside_outline": stale,
        },
        "diagnostics": _diag_counts(board),
    }
    if areas is not None:
        out["copper_area_m2"] = {lid: float(areas.geo.copper(lid).area) for lid in areas.cu}
    return out


def _status_block(findings: Sequence[xv.Finding]) -> dict[str, Any]:
    return {"cell": xv.matrix_cell(findings), "counts": xv.status_counts(findings)}


def result_to_dict(res: xv.BoardResult) -> dict[str, Any]:
    assert res.native is not None
    cross = res.cross.findings if res.cross else []
    nexp = res.native_expected.findings if res.native_expected else []
    iexp = res.ipc_expected.findings if res.ipc_expected else []
    allf = res.all_findings()
    whitelisted: dict[str, dict[str, Any]] = {}
    for f in allf:
        if f.status == "whitelisted":
            w = whitelisted.setdefault(
                f.exception, {"exception": f.exception, "cause": f.cause, "count": 0, "checks": []}
            )
            w["count"] += 1
            if f.check not in w["checks"]:
                w["checks"].append(f.check)
    fidelity: dict[str, str] = {
        "kicad": _status_block(nexp)["cell"] if nexp else "n/a",
        "ipc2581": _status_block([*cross, *iexp])["cell"] if res.ipc is not None else "n/a",
    }
    doc: dict[str, Any] = {
        "name": res.name,
        "fidelity": fidelity,
        "findings": {
            "total": _status_block(allf),
            "native_vs_expected": _status_block(nexp) if nexp else None,
            "ipc2581_vs_expected": _status_block(iexp) if iexp else None,
            "native_vs_ipc2581": _status_block(cross) if cross else None,
        },
        "failures": [f.row() for f in allf if f.status == "fail"],
        "whitelisted": sorted(whitelisted.values(), key=lambda w: w["exception"]),
        "native": board_summary(res.native, res.native_areas),
        "notes": res.notes,
    }
    if res.ipc is not None:
        doc["ipc2581"] = board_summary(res.ipc, res.ipc_areas)
    return doc


def build_report(results: Sequence[xv.BoardResult], **meta: Any) -> dict[str, Any]:
    boards = [result_to_dict(r) for r in results]
    failing = [b["name"] for b in boards if b["findings"]["total"]["counts"]["fail"]]
    return {
        "schema": SCHEMA,
        "meta": meta,
        "summary": {
            "boards": len(boards),
            "failing_boards": failing,
            "ok": not failing,
            "matrix": {b["name"]: b["fidelity"] for b in boards},
        },
        "boards": boards,
    }


# ---------------------------------------------------------------------------- text
def format_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    s = report["summary"]
    lines.append(f"Ingest fidelity report ({report['schema']}): {s['boards']} board(s)")
    lines.append(
        "Result: " + ("OK, no failing findings" if s["ok"] else f"FAIL {s['failing_boards']}")
    )
    lines.append("")
    w = max([len(b["name"]) for b in report["boards"]] + [5])
    lines.append(f"{'board':<{w}}  {'kicad':<12} {'ipc2581':<12} pass  whitelisted  fail")
    for b in report["boards"]:
        c = b["findings"]["total"]["counts"]
        lines.append(
            f"{b['name']:<{w}}  {b['fidelity']['kicad']:<12} {b['fidelity']['ipc2581']:<12} "
            f"{c['pass']:>4}  {c['whitelisted']:>11}  {c['fail']:>4}"
        )
    for b in report["boards"]:
        n = b["native"]
        c = n["counts"]
        lines += ["", f"== {b['name']}"]
        lines.append(
            f"  native: {c['nets']} nets, {c['components']} components, {c['pads']} pads, "
            f"{c['tracks']} tracks, vias {c['vias'] or 0}, {c['zones']} zones, "
            f"{c['copper_layers']} copper layers, stackup known={n['stackup']['known']}"
        )
        if n["diagnostics"]:
            lines.append(
                "  native diagnostics: "
                + ", ".join(f"{k} x{v}" for k, v in n["diagnostics"].items())
            )
        zs = n["zones"]
        lines.append(f"  zone fill state: {zs['fill_state']}")
        for st in zs["fill_outside_outline"]:
            lines.append(
                f"  STALE? zone {st['zone']} has {st['area_outside_m2']:.3g} m^2 of fill "
                "outside its outline"
            )
        if "ipc2581" in b:
            lines.append(f"  ipc2581 diagnostics: {b['ipc2581']['diagnostics']}")
        for sect, label in (
            ("native_vs_expected", "native vs expected.yaml"),
            ("ipc2581_vs_expected", "ipc2581 vs expected.yaml"),
            ("native_vs_ipc2581", "native vs ipc2581"),
        ):
            blk = b["findings"][sect]
            if blk:
                lines.append(f"  {label}: {blk['cell']} {blk['counts']}")
        for wl in b["whitelisted"]:
            cause = wl["cause"] if len(wl["cause"]) <= 110 else wl["cause"][:107] + "..."
            lines.append(f"  whitelisted {wl['exception']} x{wl['count']}: {cause}")
        for f in b["failures"]:
            lines.append(
                f"  FAIL {f['source']}/{f['reader']} {f['check']} [{f['key']}] "
                f"native={f['native']} other={f['other']} delta={f['delta']} tol={f['tol']} "
                f"{f.get('detail', '')}"
            )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------- CLI
def _resolve(path: Path) -> tuple[str, Path, Path | None, dict[str, Any] | None]:
    pcb = path / "board.kicad_pcb" if path.is_dir() else path
    if not pcb.is_file():
        raise FileNotFoundError(f"no .kicad_pcb at {path}")
    name = pcb.parent.name if pcb.name == "board.kicad_pcb" else pcb.stem
    ipc = pcb.with_name("board.ipc2581.xml") if pcb.name == "board.kicad_pcb" else None
    exp_path = pcb.with_name("expected.yaml")
    exp = xv.load_expected(pcb.parent) if exp_path.is_file() else None
    return name, pcb, ipc if ipc is not None and ipc.is_file() else None, exp


def run(
    paths: Sequence[Path],
    *,
    synthetic: bool = False,
    demos: bool = False,
    ipc: str = "auto",
    work_dir: Path | None = None,
) -> list[xv.BoardResult]:
    """Read and compare every requested board."""
    work = work_dir or Path(tempfile.mkdtemp(prefix="oa_ingest_report_"))
    jobs: list[tuple[str, Path, Path | None, dict[str, Any] | None]] = []
    if synthetic:
        jobs += [_resolve(d) for d in xv.synthetic_boards()]
    jobs += [_resolve(p) for p in paths]
    if demos:
        for name, directory, stem in xv.DEMOS:
            pcb = xv.demo_board(directory, stem)
            if pcb is not None:
                jobs.append((name, pcb, None, None))
    out: list[xv.BoardResult] = []
    for name, pcb, ipc_xml, exp in jobs:
        note = ""
        if ipc == "none":
            ipc_xml = None
        elif ipc not in ("auto", "none"):
            ipc_xml = Path(ipc)
        elif ipc_xml is None and xv.kicad_tools().cli is not None:
            try:
                ipc_xml = xv.export_ipc(pcb, work / f"{name}.ipc2581.xml")
            except Exception as exc:
                note = f"IPC-2581 export failed: {exc}"
        elif ipc_xml is None:
            note = "no IPC-2581 file and kicad-cli not found: cross-reader comparison skipped"
        res = xv.run_board(name, pcb, ipc_xml, exp)
        if note:
            res.notes.append(note)
        out.append(res)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="ingest_report", description="Ingest fidelity report: native vs IPC-2581 vs expected."
    )
    ap.add_argument("paths", nargs="*", type=Path, help=".kicad_pcb files or board directories")
    ap.add_argument("--synthetic", action="store_true", help="all WP-102 boards")
    ap.add_argument("--demos", action="store_true", help="the KiCad demo boards (needs KiCad)")
    ap.add_argument("--ipc", default="auto", help="'auto', 'none' or an IPC-2581 file (one board)")
    ap.add_argument("--json", metavar="FILE", help="write the JSON report ('-' = stdout)")
    ap.add_argument("--csv", metavar="FILE", help="write every finding as CSV ('-' = stdout)")
    ap.add_argument("--text", action="store_true", help="print the human-readable report")
    args = ap.parse_args(argv)
    if not (args.paths or args.synthetic or args.demos):
        ap.error("give a board path, --synthetic or --demos")
    try:
        results = run(args.paths, synthetic=args.synthetic, demos=args.demos, ipc=args.ipc)
    except Exception as exc:
        sys.stderr.write(f"ingest_report: {type(exc).__name__}: {exc}\n")
        return 2
    report = build_report(results, kicad=xv.kicad_tools().version(), tolerances=xv.tolerances())
    if args.json:
        text = json.dumps(report, indent=1, sort_keys=True, default=str) + "\n"
        if args.json == "-":
            sys.stdout.write(text)
        else:
            Path(args.json).write_text(text, encoding="utf-8")
    if args.csv:
        csv_text = xv.findings_to_csv(f for r in results for f in r.all_findings())
        if args.csv == "-":
            sys.stdout.write(csv_text)
        else:
            Path(args.csv).write_text(csv_text, encoding="utf-8", newline="")
    if args.text or not (args.json or args.csv):
        sys.stdout.write(format_text(report))
    return 0 if report["summary"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
