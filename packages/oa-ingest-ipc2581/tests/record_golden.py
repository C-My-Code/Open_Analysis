# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Record ``tests/golden/ingest_ipc2581/<case>/case.toml`` from pcbnew (needs KiCad 10).

Run from the repository root::

    uv run python packages/oa-ingest-ipc2581/tests/record_golden.py

The expected values come from ``oracle_pcbnew.py`` (pcbnew, in KiCad's Python), not from the
reader, so the golden files are an independent reference. Regenerate only deliberately.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from kicad_support import (
    DEMOS,
    MIN_NET_AREA_M2,
    demo_board,
    run_oracle,
    stackup_expected,
    tools,
)

from oa_testkit.golden import Tolerance, write_case

ROOT = Path(__file__).resolve().parents[3] / "tests" / "golden"


def main() -> None:
    version = tools().version()
    for case, directory, stem in DEMOS:
        board = demo_board(directory, stem)
        if board is None:
            print(f"skip {case}: demo board not found")
            continue
        with tempfile.TemporaryDirectory() as d:
            ora = run_oracle(board, Path(d) / "o.json")
        area = {
            lid: {
                "total": rec["total"],
                "nets": {n: a for n, a in rec["nets"].items() if n and a >= MIN_NET_AREA_M2},
            }
            for lid, rec in ora["copper_area"].items()
        }
        expected = {
            "counts": ora["counts"],
            "copper_area": area,
            "stackup": stackup_expected(ora["stackup"]),
        }
        tol = Tolerance(
            rtol=1e-9,
            per_key={
                "counts": Tolerance(rtol=0.0),
                "copper_area": Tolerance(rtol=0.005),
                "stackup": Tolerance(rtol=1e-6),
            },
        )
        write_case(
            ROOT / "ingest_ipc2581" / case / "case.toml",
            source=(
                f"pcbnew {ora['kicad_version']} (kicad-cli {version}) loading the KiCad demo board "
                f"{directory}/{stem}.kicad_pcb; stackup read from the (stackup) block. "
                "Recorded by packages/oa-ingest-ipc2581/tests/record_golden.py"
            ),
            tolerance=tol,
            inputs={"demo": f"{directory}/{stem}.kicad_pcb", "ipc2581_version": "C"},
            expected=expected,
        )
        print("wrote", case)


if __name__ == "__main__":
    main()
