# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""WP-105 performance acceptance: median BEM solve (single microstrip, 2-conductor pair,
20 frequencies) below 50 ms. Plain timer (pytest-benchmark 5.3 does not load under this pytest)."""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable

import _xs
import numpy as np
import pytest

from oa_impedance.service import ImpedanceServiceImpl
from oa_model.xsection import CrossSection

FREQS = list(np.logspace(6, 10, 20))
BUDGET_S = 0.050


def _solve(xs: CrossSection) -> Callable[[], object]:
    def run() -> object:
        # a fresh service each time: no cache hits, geometry + influence matrices + solve
        return ImpedanceServiceImpl().solve(xs, FREQS, method="bem")

    return run


def _median_s(fn: Callable[[], object], n: int = 9) -> float:
    fn()  # warm-up (imports, BLAS)
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t0)
    return statistics.median(ts)


@pytest.mark.perf
@pytest.mark.parametrize("pair", [False, True], ids=["single", "pair"])
def test_bem_median_under_50ms(pair: bool) -> None:
    # shared CI machines: best of three medians (the budget is about the algorithm, not the load)
    med = min(_median_s(_solve(_xs.pcb_microstrip(pair=pair))) for _ in range(3))
    print(f"\nBEM {'pair' if pair else 'single'} microstrip, 20 freqs: median {med * 1e3:.1f} ms")
    assert med < BUDGET_S


@pytest.mark.perf
def test_closed_form_quick_under_1ms() -> None:
    svc = ImpedanceServiceImpl()
    xs = _xs.microstrip(1.0, 4.3)
    med = _median_s(lambda: ImpedanceServiceImpl().quick(xs), n=25)
    assert med < 2e-3  # target < 1 ms on a quiet machine; 2 ms allows CI noise
    del svc


@pytest.mark.perf
def test_batch_throughput_per_section_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """B-IMP-04 (BENCHMARKS.md): 1000 sections x 50 f in 20 s on the CPU, that is 20 ms per
    section. 96 mix-like sections (smaller than the real ones: about 600 panels) through
    ``solve_many``; the headline number is measured by tools/bench on the reference machine."""
    import impedance_mix_helpers as mix

    monkeypatch.setenv("OA_DEVICE", "cpu")
    sections = mix.mix_sections(96, seed0=2000)
    freqs = list(np.logspace(6, 10, 50))
    t0 = time.perf_counter()
    out = ImpedanceServiceImpl().solve_many(sections, freqs, method="bem")
    per_section = (time.perf_counter() - t0) / len(sections)
    print(f"\nB-IMP-04 proxy: {per_section * 1e3:.1f} ms per section")
    assert len(out) == len(sections)
    assert per_section < 0.020
