# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: E501
"""Ingest validation: native KiCad reader vs expected.yaml vs the IPC-2581 reader (WP-208)."""

from __future__ import annotations

import sys
import time

from val_model import REPO, Case, case_dir, load_case

sys.path.insert(0, str(REPO / "tests" / "integration" / "ingest"))

import ingest_xval as xv

CAT = "ingest"


def run() -> list[Case]:
    out: list[Case] = []
    golden = load_case("ingest_cross", "synthetic_matrix")
    for d in xv.synthetic_boards():
        name = d.name
        ipc = d / "board.ipc2581.xml"
        t0 = time.perf_counter()
        res = xv.run_board(
            name, d / "board.kicad_pcb", ipc if ipc.is_file() else None, xv.load_expected(d)
        )
        dt = time.perf_counter() - t0
        for label, judge, cls, indep, what in (
            (
                "native_vs_expected",
                res.native_expected,
                "analytic",
                True,
                "KiCad reader vs expected.yaml (counts exact, z <= 1 nm, per-net copper area <= 0.1 %)",
            ),
            (
                "ipc2581_vs_expected",
                res.ipc_expected,
                "analytic",
                True,
                "IPC-2581 reader vs expected.yaml",
            ),
            (
                "native_vs_ipc2581",
                res.cross,
                "cross-solver",
                True,
                "KiCad reader vs IPC-2581 reader (two independent parsers of the same board)",
            ),
        ):
            if judge is None:
                continue
            counts = xv.status_counts(judge.findings)
            pinned = golden["expected"].get(name, {}).get(label, {})
            note = (
                f"{counts['pass']} pass, {counts['whitelisted']} whitelisted "
                f"(documented in tests/golden/ingest_cross/exceptions.toml), {counts['fail']} fail"
            )
            if pinned and pinned != counts:
                note += f"; differs from the pinned matrix {pinned}"
            out.append(
                Case(
                    f"ingest.{name}.{label}",
                    CAT,
                    f"{name}: {label.replace('_', ' ')}",
                    f"{what}; WP-208 harness tests/integration/ingest/ingest_xval.py",
                    cls,
                    indep,
                    "failed findings",
                    "",
                    0.0,
                    float(counts["fail"]),
                    "exact",
                    0.0,
                    case_dir("ingest_cross", "synthetic_matrix"),
                    note=note,
                    runtime_s=dt,
                )
            )
    return out
