# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate every ``expected.yaml`` against ``expected.schema.json`` and cross-check it with the board directory.

    python tools/boardgen/validate_expected.py [tests/boards/kicad10]

Needs ``pyyaml`` and ``jsonschema`` (dev dependencies; not required to regenerate the boards).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2] / "tests" / "boards" / "kicad10"


def main(root: Path) -> int:
    schema = json.loads((root / "expected.schema.json").read_text(encoding="utf8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    bad = 0
    for exp_path in sorted(root.glob("*/expected.yaml")):
        d = exp_path.parent
        doc = yaml.safe_load(exp_path.read_text(encoding="utf8"))
        errs = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
        extra = []
        for f in ("board.kicad_pcb", "board.kicad_pro", "board.ipc2581.xml", "drc.json", "README.md"):
            if not (d / f).exists():
                extra.append(f"missing {f}")
        if doc["board"] != d.name:
            extra.append("board name differs from directory")
        if doc["drc"]["errors"] != sum(i["count"] for i in doc["intended_drc"]):
            extra.append("DRC errors are not all listed in intended_drc")
        stack = {r["id"]: r for r in doc["stackup"]["layers"]}
        top = doc["stackup"]["layers"][0]["z_top"]
        if abs(top - doc["stackup"]["board_thickness"]) > 1e-12:
            extra.append("stackup top != board thickness")
        for r in doc["stackup"]["layers"]:
            if abs(r["z_top"] - r["z_bottom"] - r["thickness"]) > 1e-12:
                extra.append(f"z mismatch {r['id']}")
        for e in doc["erc"]:
            if e["layer"] not in stack:
                extra.append(f"erc layer {e['layer']} not in stackup")
            if e["rule_id"] in doc["must_not_report"]:
                extra.append(f"{e['rule_id']} both expected and must_not_report")
        status = "ok" if not errs and not extra else "FAIL"
        print(f"{d.name:24s} {status}")
        for e in errs:
            print("   ", "/".join(map(str, e.path)), e.message[:140])
        for m in extra:
            print("   ", m)
        bad += bool(errs or extra)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT))
