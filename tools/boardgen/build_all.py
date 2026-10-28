# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regenerate every WP-102 test board.

    python tools/boardgen/build_all.py [--out tests/boards/kicad10] [--only NAME ...] [--no-render]

Needs KiCad 10.0.5 (``kicad-cli`` + the bundled Python with ``pcbnew``).  Set ``OA_KICAD_DIR`` if KiCad is not in
``D:\\Program Files\\KiCad\\10.0``.  Runs on any Python >= 3.9 (standard library only).

Per board: write the board text (``kwriter``), refill zones + save with pcbnew (``kicad_post``), run DRC, export
IPC-2581 rev C, ODB++ (zip) and a render, then write ``expected.yaml`` and the README.  Deterministic: all uuids
are derived and dates/paths are scrubbed, so two runs give byte-identical ``.kicad_pcb``, ``.kicad_pro``, ``drc.json``
and ``expected.yaml`` files (KiCad's IPC-2581 and ODB++ exporters reorder some elements from run to run).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import boards  # noqa: E402
import yamlout  # noqa: E402
from kwriter import metres  # noqa: E402

KICAD_DIR = Path(os.environ.get("OA_KICAD_DIR", r"D:\Program Files\KiCad\10.0"))
CLI = KICAD_DIR / "bin" / "kicad-cli.exe"
PY = KICAD_DIR / "bin" / "python.exe"
SPDX = ("SPDX-FileCopyrightText: 2026 Open_Analysis contributors\n"
        # REUSE-IgnoreStart
        "SPDX-License-Identifier: CC-BY-4.0\n")
        # REUSE-IgnoreEnd
SCHEMA_ID = "oa-board-expected/1"

DRC_WHY = {}  # violation type -> explanation, for intended_drc entries


def run(cmd: list, timeout: int = 300, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed ({r.returncode}):\n{r.stdout}\n{r.stderr}")
    return r


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf8", newline="\n")


def block(text: str, start: int) -> int:
    """Index just past the S-expression that starts at ``start`` (quotes respected)."""
    depth, i, q = 0, start, False
    while True:
        c = text[i]
        if q:
            if c == "\\":
                i += 1
            elif c == '"':
                q = False
        elif c == '"':
            q = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1


def extract_fills(text: str) -> dict[str, str]:
    """zone uuid -> text of its (filled_polygon ...) blocks, from a pcbnew-saved board."""
    fills = {}
    for m in re.finditer(r"\n\t\(zone\n", text):
        s = m.start() + 1
        e = block(text, s)
        z = text[s:e]
        uid = re.search(r'\(uuid "([^"]+)"\)', z).group(1)
        parts = []
        for fm in re.finditer(r"\n\t\t\(filled_polygon\n", z):
            fs = fm.start() + 1
            parts.append(z[fs:block(z, fs)])
        if parts:
            fills[uid] = "\n".join(parts)
    return fills


def v9_text(b9, filled_text: str) -> str:
    """KiCad 9-flavoured file: same copper as the pcbnew-refilled twin, fills injected."""
    raw = b9.pcb_text()
    unfilled = "\n\t\t(fill\n"
    for zid, blocks in extract_fills(filled_text).items():
        i = raw.index(f'(uuid "{zid}")')
        k = raw.index(unfilled, i)
        raw = raw[:k] + "\n\t\t(fill yes\n" + raw[k + len(unfilled):]
        j = raw.index("\n\t)\n", i)
        raw = raw[:j] + "\n" + blocks + raw[j:]
    return raw


def scrub_drc(path: Path) -> dict:
    d = json.loads(path.read_text(encoding="utf8"))
    d.pop("date", None)
    d["source"] = "board.kicad_pcb"
    write(path, json.dumps(d, indent=2) + "\n")
    return d


def scrub_ipc(path: Path) -> None:
    t = path.read_text(encoding="utf8")
    t = re.sub(r'(origination|lastChange|datetime)="[0-9T:-]+"', r'\1="2000-01-01T00:00:00"', t)
    write(path, t)


def _odb_scrub(fn: str, data: bytes) -> bytes:
    if fn.endswith("misc/info") or fn.endswith("eda/data"):
        t = data.decode("utf8")
        t = re.sub(r"(CREATION_DATE|SAVE_DATE)=[0-9.]+", r"\1=20000101.000000", t)
        t = re.sub(r"^# [0-9T:-]+$", "# 2000-01-01T00:00:00", t, flags=re.M)
        return t.encode("utf8")
    return data


def scrub_odb(path: Path) -> None:
    """Rewrite the ODB++ zip with fixed timestamps and sorted entries (bytes depend only on content)."""
    with zipfile.ZipFile(path) as zin:
        items = sorted((i.filename, zin.read(i)) for i in zin.infolist())
    items = [(fn, _odb_scrub(fn, d)) for fn, d in items]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for fn, data in items:
            zi = zipfile.ZipInfo(fn, date_time=(2000, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = (0o755 << 16) if fn.endswith("/") else (0o644 << 16)
            zout.writestr(zi, data)


def build_one(name: str, out_root: Path, render: bool = True) -> dict:
    twin = None
    if name == "microstrip_v9":
        b = boards.microstrip_board(9)
        twin = boards.microstrip_board(10, name="microstrip_v9")
    else:
        b = boards.BOARDS[name]()
    src = twin or b
    out = out_root / name
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix=f"oa_{name}_") as td:
        td = Path(td)
        write(td / "board.kicad_pcb", src.pcb_text())
        write(td / "board.kicad_pro", src.pro_json())
        write(out / "board.kicad_pro", b.pro_json())
        stats_json = td / "stats.json"
        filled = td / "filled.kicad_pcb"
        shutil.copy(td / "board.kicad_pro", td / "filled.kicad_pro")
        run([PY, HERE / "kicad_post.py", td / "board.kicad_pcb", filled, stats_json], timeout=300)
        stats = json.loads(stats_json.read_text(encoding="utf8"))
        filled_text = filled.read_text(encoding="utf8")
        write(out / "board.kicad_pcb", v9_text(b, filled_text) if twin else filled_text)

    brd = out / "board.kicad_pcb"
    # DRC (exit code is 0 unless --exit-code-violations is given)
    r = run([CLI, "pcb", "drc", "--format", "json", "--severity-all", "--units", "mm", "--output", out / "drc.json", brd])
    drc = scrub_drc(out / "drc.json")
    run([CLI, "pcb", "export", "ipc2581", "--version", "C", "--output", out / "board.ipc2581.xml", brd])
    run([CLI, "pcb", "export", "odb", "--compression", "zip", "--output", out / "board.odb.zip", brd])
    scrub_ipc(out / "board.ipc2581.xml")
    scrub_odb(out / "board.odb.zip")
    if render:
        r = run([CLI, "pcb", "render", "--output", out / "board.png", "--width", "1400", "--height", "800",
                 "--quality", "basic", "--background", "opaque", brd], timeout=240, check=False)
        if r.returncode != 0:
            print(f"  [warn] render failed for {name}: {r.stderr.strip()[:200]}")

    check_counts(name, b, stats)
    exp = assemble_expected(name, b, stats, drc, brd)
    write(out / "expected.yaml", yamlout.dump(exp))
    write(out / "README.md", readme(name, b, exp))
    for f in sorted(out.iterdir()):
        if f.suffix == ".kicad_prl":
            f.unlink()
        elif f.suffix != ".license":
            write(f.with_name(f.name + ".license"), SPDX)
    n_err = sum(1 for k in ("violations", "unconnected_items", "schematic_parity") for v in drc.get(k, [])
                if v.get("severity") == "error")
    n_warn = sum(1 for k in ("violations", "unconnected_items", "schematic_parity") for v in drc.get(k, [])
                 if v.get("severity") == "warning")
    return {"errors": n_err, "warnings": n_warn, "drc_rc": r.returncode}


def check_counts(name: str, b, stats: dict) -> None:
    c, g = stats["counts"], b.gen_counts
    exp_vias = {k[4:]: v for k, v in g.items() if k.startswith("via_")}
    problems = []
    if exp_vias != c["vias"]:
        problems.append(f"vias {exp_vias} != {c['vias']}")
    for k_gen, k_cnt in (("pads", "pads"), ("tracks", "tracks"), ("zones", "zones"), ("footprints", "footprints")):
        if g.get(k_gen, 0) != c[k_cnt]:
            problems.append(f"{k_gen} {g.get(k_gen, 0)} != {c[k_cnt]}")
    if g.get("rule_areas", 0) != c["rule_areas"]:
        problems.append(f"rule_areas {g.get('rule_areas', 0)} != {c['rule_areas']}")
    if c["nets"] != len([n for n in b.nets if n]):
        problems.append(f"nets {len([n for n in b.nets if n])} != {c['nets']}")
    for z in stats["zones"]:
        if z["filled_area_m2"] <= 0:
            problems.append(f"empty fill: {z}")
    if problems:
        raise RuntimeError(f"{name}: generator/pcbnew disagreement: {problems}")


def assemble_expected(name: str, b, stats: dict, drc: dict, brd: Path) -> dict:
    text = brd.read_text(encoding="utf8")
    ver = re.search(r"\(version (\d+)\)", text).group(1)
    gen_ver = re.search(r'\(generator_version "([^"]+)"\)', text).group(1)
    rows = b.stk.rows()
    src2id = {r["source_name"]: r["id"] for r in rows}
    area = {src2id[l]: {n: round(a, 15) for n, a in per.items() if n} for l, per in stats["copper_area_m2"].items()}
    area = {k: area[k] for k in sorted(area, key=lambda s: int(s[1:]))}
    nets = sorted(n for n in b.nets if n)
    rule_ids = sorted({e["rule_id"] for e in b.exp["erc"]})
    viol = [v for k in ("violations", "unconnected_items") for v in drc.get(k, [])]
    summary: dict[str, dict[str, int]] = {}
    for v in viol:
        summary.setdefault(v.get("severity", "?"), {})
        summary[v.get("severity", "?")][v["type"]] = summary[v.get("severity", "?")].get(v["type"], 0) + 1
    intended = [{"type": t, "count": n, "why": DRC_WHY.get(t, "see README")}
                for t, n in sorted(summary.get("error", {}).items())]
    e = {
        "schema": SCHEMA_ID,
        "board": name,
        "description": b.exp["description"],
        "kicad": {"flavour": b.kicad, "file_version": int(ver), "generator_version": gen_ver,
                  "net_table": b.kicad < 10, "generated_with": f"KiCad {stats['kicad_version']}"},
        "units": {"quantities": "SI (m, m^2, Hz, S/m, ohm)", "location_mm": "KiCad file frame, mm, Y down "
                  "(reader Y-up model: y_model = -y_file)", "tolerance_mm": "mm"},
        "materials": {"dielectric": "FR4", "dk": 4.3, "df": 0.02, "copper_thickness": metres(0.035),
                      "copper_conductivity": 5.8e7, "solder_mask": "none"},
        "counts": {"nets": stats["counts"]["nets"], "vias": stats["counts"]["vias"], "pads": stats["counts"]["pads"],
                   "pads_smd": stats["counts"]["pads_smd"], "pads_tht": stats["counts"]["pads_tht"],
                   "tracks": stats["counts"]["tracks"], "arcs": stats["counts"]["arcs"],
                   "zones": stats["counts"]["zones"], "rule_areas": stats["counts"]["rule_areas"],
                   "footprints": stats["counts"]["footprints"], "copper_layers": stats["counts"]["copper_layers"]},
        "stackup": {"board_thickness": round(b.stk.total_nm() * 1e-9, 12), "z_origin": "bottom face of the lowest copper layer",
                    "layers": [{k: r[k] for k in ("id", "source_name", "kind", "thickness", "z_bottom", "z_top")}
                               for r in rows]},
        "copper_area": {"unit": "m^2", "method": "pcbnew after ZONE_FILLER: union of zone fills, tracks, pads and via "
                        "pads per net per layer; circles and arcs discretised at 1 um",
                        "tolerance_rel": 0.001, "layers": area},
        "nets": nets,
        "net_classes": [{"name": nc["name"], **{k: v for k, v in nc.items() if k != "name"}} for nc in b.netclasses],
        "net_class_patterns": [{"net_class": c, "pattern": p} for c, p in b.patterns],
        "diff_pairs": b.exp.get("diff_pairs", []),
        "geometry": b.exp["geometry"],
        "erc": b.exp["erc"],
        "must_not_report": [r for r in boards.RULES if r not in rule_ids],
        "negative_cases": b.exp["negative_cases"],
        "unspecified": b.exp["unspecified"],
        "intended_drc": intended,
        "drc": {"kicad_cli": "pcb drc --format json --severity-all", "errors": sum(summary.get("error", {}).values()),
                "warnings": sum(summary.get("warning", {}).values()), "by_type": summary,
                "report": "drc.json"},
        "checks": stats["checks"],
        "notes": b.exp["notes"],
    }
    for k in ("dc", "em"):
        if k in b.exp:
            e[k] = b.exp[k]
    return e


def readme(name: str, b, e: dict) -> str:
    lines = [f"# {name}", "", e["description"], ""]
    lines += ["## Files", "",
              "- `board.kicad_pcb`, `board.kicad_pro`: the board (KiCad %s syntax, generated with KiCad %s)."
              % (b.kicad, e["kicad"]["generated_with"].split()[-1]),
              "- `expected.yaml`: ground truth for the readers and analyses (schema: `../expected.schema.json`).",
              "- `drc.json`: `kicad-cli pcb drc --format json --severity-all` output.",
              "- `board.ipc2581.xml`: IPC-2581 rev C export, `board.odb.zip`: ODB++ export, `board.png`: 3D render.", ""]
    lines += ["## Deliberate features and their rules", ""]
    if e["erc"]:
        lines += ["| Rule | Nets | Layer | Location (mm, file frame) | Created by |", "|---|---|---|---|---|"]
        for r in e["erc"]:
            loc = "-" if r["location_mm"] is None else f"{r['location_mm'][0]:.3f}, {r['location_mm'][1]:.3f}"
            lines.append(f"| {r['rule_id']} | {', '.join(r['nets'])} | {r['layer']} | {loc} | `{r['generator']}` |")
        lines.append("")
    else:
        lines += ["None: this board is a clean reference (`must_not_report` lists every catalogued rule).", ""]
    if e["notes"]:
        lines += ["## Notes", ""] + [f"- {n}" for n in e["notes"]] + [""]
    d = e["drc"]
    lines += ["## KiCad DRC", "",
              f"{d['errors']} errors, {d['warnings']} warnings (`drc.json`). By type: `{json.dumps(d['by_type'])}`.", ""]
    lines += ["## Regenerate", "", "```", "python tools/boardgen/build_all.py --only " + name, "```", "",
              "Licence: CC-BY-4.0 (see the `.license` sidecars).", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(HERE.parent.parent / "tests" / "boards" / "kicad10"))
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--no-render", action="store_true")
    a = ap.parse_args()
    if not CLI.exists() or not PY.exists():
        print(f"KiCad not found at {KICAD_DIR}; set OA_KICAD_DIR", file=sys.stderr)
        return 2
    out = Path(a.out)
    rc = 0
    for name in a.only or list(boards.BOARDS):
        print(f"[{name}]", flush=True)
        res = build_one(name, out, render=not a.no_render)
        print(f"  DRC: {res['errors']} errors, {res['warnings']} warnings", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
