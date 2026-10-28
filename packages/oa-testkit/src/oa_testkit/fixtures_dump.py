# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate the committed fixture files under ``tests/fixtures``.

    python -m oa_testkit.fixtures_dump [--root DIR] [--check]

Writes ``model/<builder>.oab.json`` (board-model.md section 14.3, flat arrays, UTF-8, LF),
``xsection/<case>.json`` (case metadata plus the CrossSection) and ``em/<problem>.json``.
``--check`` writes nothing and exits 1 if any file differs from what the builders produce (or
is missing), which is how the test suite keeps the committed files honest.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

from oa_model.board import Board

from . import builders, emproblems, xsections

BOARDS: dict[str, Callable[[], Board]] = {
    "microstrip": builders.microstrip_board,
    "stripline": builders.stripline_board,
    "diff_pair": builders.diff_pair_board,
    "cpwg": builders.cpwg_board,
    "split_plane": builders.split_plane_board,
    "via_transition": builders.via_transition_board,
    "power_plane": builders.power_plane_board,
}

EM_PROBLEMS = {
    "microstrip_thru_25mm_lumped": emproblems.microstrip_thru_problem,
    "stripline_thru_25mm_wave": emproblems.stripline_thru_problem,
    "via_transition_L1_L4_wave": emproblems.via_transition_problem,
}


def default_root() -> Path:
    """``<repo>/tests/fixtures`` found by walking up from this file or the working directory."""
    for start in (Path(__file__).resolve(), Path.cwd().resolve()):
        for d in start.parents if start.is_file() else (start, *start.parents):
            if (d / "tests" / "fixtures").is_dir():
                return d / "tests" / "fixtures"
    raise FileNotFoundError("cannot find tests/fixtures; pass --root")


def board_text(board: Board) -> str:
    """Canonical ``.oab.json`` text for a board."""
    return board.model_dump_json(indent=1) + "\n"


def _json_text(obj: object) -> str:
    return json.dumps(obj, indent=1, sort_keys=False, ensure_ascii=False) + "\n"


def expected_files() -> Iterator[tuple[str, str]]:
    """Yield ``(relative path, text)`` for every fixture file."""
    for name, fn in BOARDS.items():
        yield f"model/{name}.oab.json", board_text(fn())
    for case in xsections.all_cases():
        doc = {
            "name": case.name,
            "kind": case.kind,
            "source": case.source,
            "note": case.note,
            "rtol": case.rtol,
            "expected": dict(case.expected),
            "xsection": json.loads(case.xs.model_dump_json()),
        }
        yield f"xsection/{case.name}.json", _json_text(doc)
    for name, fn in EM_PROBLEMS.items():
        yield f"em/{name}.json", fn().model_dump_json(indent=1) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="oa_testkit.fixtures_dump", description=__doc__)
    ap.add_argument("--root", type=Path, default=None, help="fixtures directory (tests/fixtures)")
    ap.add_argument("--check", action="store_true", help="verify instead of writing")
    args = ap.parse_args(argv)
    root = args.root if args.root is not None else default_root()

    stale: list[str] = []
    for rel, text in expected_files():
        path = root / rel
        if args.check:
            if not path.is_file() or path.read_bytes() != text.encode("utf-8"):
                stale.append(rel)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
    if stale:
        print("stale or missing fixtures:\n  " + "\n  ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
