# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``FDTD.UNSTABLE``: non-finite energy and growth after the excitation has ended."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from fdtd_helpers import line_model

import oa_fdtd.run as run_mod
from oa_fdtd.grid import C0
from oa_fdtd.model import ModelError


class _FakeEngine:
    """Engine whose energy follows ``energy_of(step)``; no fields."""

    name = "fake"

    def __init__(self, energy_of: Any) -> None:
        self._energy_of = energy_of
        self._n = 0

    def step(self, n: int) -> None:
        self._n += n

    def energy(self) -> float:
        return float(self._energy_of(self._n))

    def results(self) -> dict[str, Any]:
        return {}

    def peak_bytes(self) -> int:
        return 0

    def series(self) -> dict[str, Any]:
        return {}


def _run(monkeypatch: pytest.MonkeyPatch, energy_of: Any, **kw: Any) -> run_mod.RunResult:
    d = C0 / 40e9 / 20.0
    m = line_model(80, d, np.array([1e9]), 20, 40, dc=False)
    eng = _FakeEngine(energy_of)
    monkeypatch.setattr(run_mod, "make_engine", lambda *a, **k: (eng, {}))
    return run_mod.run(m, engine="numpy", dtype="float64", max_steps=20000, **kw)


def test_non_finite_energy_is_unstable(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ModelError) as ei:
        _run(monkeypatch, lambda n: float("inf") if n > 640 else 1.0)
    assert ei.value.code == "FDTD.UNSTABLE"


def test_growth_after_excitation_is_unstable(monkeypatch: pytest.MonkeyPatch) -> None:
    # decays, then grows exponentially (about 1 dB per check) long after the source has ended
    def energy(n: int) -> float:
        return float(np.exp(-n / 2000.0)) if n < 8000 else float(np.exp(-4.0 + (n - 8000) / 200.0))

    with pytest.raises(ModelError) as ei:
        _run(monkeypatch, energy, end_db=-300.0)
    assert ei.value.code == "FDTD.UNSTABLE"
    assert "grew" in ei.value.message


def test_rise_during_excitation_is_not_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    # energy rises 60 dB until step 320 (the source is on until about step 352), then decays
    def energy(n: int) -> float:
        rise = 1e-6 * 10.0 ** (min(n, 320) / 320.0 * 6.0)
        return rise * float(np.exp(-max(n - 320, 0) / 200))

    res = _run(monkeypatch, energy, end_db=-40.0)
    assert res.stopped_by == "end_criterion"
