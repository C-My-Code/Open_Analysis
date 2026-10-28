# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""WP-208: native vs IPC-2581 on three KiCad demo boards (export through ``kicad-cli``)."""

from __future__ import annotations

import ingest_xval as xv
import pytest

from oa_testkit.golden import assert_close_to_golden

pytestmark = pytest.mark.kicad


@pytest.mark.parametrize("name", [n for n, _, _ in xv.DEMOS])
def test_demo_native_vs_ipc2581_has_no_unexplained_difference(
    name: str, demo_results: dict[str, xv.BoardResult]
) -> None:
    r = demo_results.get(name)
    if r is None:
        pytest.skip(f"demo {name} not installed")
    assert r.cross is not None and r.native is not None
    fails = [
        f"{f.check} [{f.key}] native={f.native} other={f.other} delta={f.delta} {f.detail}"
        for f in r.cross.findings
        if f.status == "fail"
    ]
    assert not fails
    # counts that must match exactly
    for check in ("nets.count", "vias.count", "tracks.count", "components.count"):
        assert all(f.status == "pass" for f in r.cross.findings if f.check == check), check
    # the native board is also clean enough to use
    assert not [d for d in r.native.diagnostics if d.severity == "error"]


def test_demo_exceptions_are_all_needed(demo_results: dict[str, xv.BoardResult]) -> None:
    used = {
        f.exception
        for r in demo_results.values()
        for f in r.all_findings()
        if f.status == "whitelisted"
    }
    stale = {e.id for e in xv.load_exceptions() if e.scope == "demo"} - used
    if len(demo_results) < len(xv.DEMOS):
        pytest.skip("not every demo board is installed")
    assert not stale, sorted(stale)


def test_demo_matrix_golden(demo_results: dict[str, xv.BoardResult]) -> None:
    if len(demo_results) < len(xv.DEMOS):
        pytest.skip("not every demo board is installed")
    actual = {
        name: {"native_vs_ipc2581": xv.status_counts(r.cross.findings) if r.cross else {}}
        for name, r in sorted(demo_results.items())
    }
    assert_close_to_golden(
        actual,
        "ingest_cross",
        "demo_matrix",
        source=(
            "WP-208 harness on the KiCad 10.0.5 demos pic_programmer, stickhub and cm5_minima, "
            "IPC-2581 exported by kicad-cli 10.0.5: passed / whitelisted / failed findings."
        ),
        rtol=0.0,
    )
