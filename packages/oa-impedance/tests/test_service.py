# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""ImpedanceService: dispatch, caching, dedup, plug-in methods, entry points."""

from __future__ import annotations

from collections.abc import Sequence

import _xs
import numpy as np
import pytest

from oa_framework.registry import Registry
from oa_framework.services import ImpedanceService
from oa_impedance import methods
from oa_impedance.errors import ImpedanceUnsupported, MethodNotAvailable
from oa_impedance.service import ImpedanceServiceImpl
from oa_model.xsection import CrossSection
from oa_results.rlgc import ImpedanceResult
from oa_testkit import xsections as tk


def test_protocol_and_entry_points() -> None:
    svc = ImpedanceServiceImpl()
    assert isinstance(svc, ImpedanceService)
    reg = Registry.load()
    assert "impedance.xsection" in reg.analyses
    assert reg.services().has("impedance")
    assert isinstance(reg.services().get("impedance"), ImpedanceServiceImpl)


def test_auto_prefers_closed_form_for_canonical_and_bem_otherwise() -> None:
    svc = ImpedanceServiceImpl()
    assert svc.solve(_xs.microstrip(1.0, 4.3)).method == "closed_form"
    assert svc.solve(_xs.stripline(0.5)).method == "closed_form"
    assert svc.solve(_xs.edge_coupled_stripline(0.5, 0.25)).method == "closed_form"
    assert svc.solve(tk.get_case("wire_over_plane_h2r").xs).method == "closed_form"
    assert svc.solve(_xs.pcb_microstrip(mask=True)).method == "bem"  # mask layer: no formula
    assert svc.solve(tk.get_case("coax_lnba1_air").xs).method == "bem"


def test_closed_form_result_is_complete_and_labelled() -> None:
    r = ImpedanceServiceImpl().solve(_xs.microstrip(1.0, 4.3, t_over_h=0.035), [1e9, 5e9])
    assert r.method == "closed_form" and r.rlgc is None and r.validity == "in_range"
    assert r.accuracy_class == "screening"
    assert r.params.z0 is not None and len(r.params.z0) == 2
    assert r.params.loss_db_per_m.array[1, 0] > r.params.loss_db_per_m.array[0, 0] > 0
    assert r.params.eps_eff_modal.array[1, 0] > r.params.eps_eff_modal.array[0, 0]  # dispersion


def test_out_of_range_closed_form_is_flagged() -> None:
    xs = _xs.microstrip(0.002, 4.3)  # W/h = 0.002: below every published range
    r = ImpedanceServiceImpl().solve(xs, method="closed_form")
    assert r.validity in ("extrapolated", "invalid")
    assert any("W/h" in n for n in r.notes)
    assert any(not f.ok for f in r.validity_flags)
    # auto falls back to the BEM when the formula is invalid
    xs2 = _xs.microstrip(0.0005, 4.3)
    assert ImpedanceServiceImpl().solve(xs2).method == "bem"


def test_quick_and_unsupported_closed_form() -> None:
    svc = ImpedanceServiceImpl()
    assert svc.quick(_xs.microstrip(1.0, 4.3)).method == "closed_form"
    with pytest.raises(ImpedanceUnsupported) as ei:
        svc.quick(_xs.pcb_microstrip(mask=True))
    assert ei.value.info().code == "IMPEDANCE_UNSUPPORTED" and ei.value.info().hint


def test_fem_unavailable_until_registered() -> None:
    svc = ImpedanceServiceImpl()
    xs = _xs.microstrip(1.0, 4.3)
    with pytest.raises(MethodNotAvailable) as ei:
        svc.solve(xs, method="fem")
    assert "WP-202" in (ei.value.info().hint or "")

    calls: list[int] = []

    def fake_fem(x: CrossSection, freqs: Sequence[float]) -> ImpedanceResult:
        calls.append(1)
        return ImpedanceServiceImpl().solve(x, freqs, method="bem")

    methods.register("fem", fake_fem)
    try:
        r = ImpedanceServiceImpl().solve(xs, method="fem")
        assert calls == [1] and r.method == "bem"
        with pytest.raises(ValueError, match="already registered"):
            methods.register("fem", fake_fem)
        with pytest.raises(ValueError, match="built in"):
            methods.register("bem", fake_fem)
    finally:
        methods.unregister("fem")
    with pytest.raises(MethodNotAvailable):
        ImpedanceServiceImpl().solve(xs, method="fem")


def test_solve_many_dedups_by_key_and_kernel_called_once() -> None:
    svc = ImpedanceServiceImpl()
    xs = _xs.pcb_microstrip()
    out = svc.solve_many([xs] * 100, [0.0, 1e9], method="bem")
    assert len(out) == 100 and svc.stats["bem"] == 1
    assert all(o is out[0] for o in out)
    # a different but key-equal section (provenance/net dropped) hits the cache
    xs2 = xs.model_copy(
        update={"conductors": tuple(c.model_copy(update={"net": "N1"}) for c in xs.conductors)}
    )
    svc.solve(xs2, [0.0, 1e9], method="bem")
    assert svc.stats["bem"] == 1 and svc.stats["hits"] >= 1


def test_solve_many_mixed_unique_parallel() -> None:
    svc = ImpedanceServiceImpl()
    xss = [_xs.microstrip(u, 4.3) for u in (0.5, 1.0, 1.5, 0.5, 1.0)]
    out = svc.solve_many(xss, method="bem")
    assert svc.stats["bem"] == 3
    assert out[0] is out[3] and out[1] is out[4]
    zs = [float(o.params.z0.array[0]) for o in out]  # type: ignore[union-attr]
    assert zs[0] > zs[1] > zs[2]


def test_job_cache_hook_roundtrip() -> None:
    store: dict[str, str] = {}
    a = ImpedanceServiceImpl(job_cache=store)
    xs = _xs.pcb_microstrip()
    ra = a.solve(xs, [0.0], method="bem")
    assert len(store) == 1
    b = ImpedanceServiceImpl(job_cache=store)
    rb = b.solve(xs, [0.0], method="bem")
    assert b.stats["bem"] == 0 and b.stats["hits"] == 1
    assert rb.params.z0 is not None and ra.params.z0 is not None
    np.testing.assert_allclose(rb.params.z0.array, ra.params.z0.array)


def test_bad_frequencies_rejected() -> None:
    svc = ImpedanceServiceImpl()
    with pytest.raises(ImpedanceUnsupported):
        svc.solve(_xs.microstrip(1.0, 4.3), [2e9, 1e9])
    with pytest.raises(ImpedanceUnsupported):
        svc.solve(_xs.microstrip(1.0, 4.3), [-1.0])


def test_bem_result_contents_and_summary() -> None:
    xs = _xs.pcb_microstrip(pair=True)
    r = ImpedanceServiceImpl().solve(xs, [0.0, 1e9, 10e9], method="bem")
    assert r.method == "bem" and r.rlgc is not None and r.accuracy_class == "validated"
    assert r.rlgc.R.array.shape == (3, 2, 2) and r.rlgc.conductor_ids == ("s1", "s2")
    assert (r.rlgc.C.array[:, 0, 1] < 0).all()  # Maxwell form
    assert (r.params.loss_db_per_m.array[2] > r.params.loss_db_per_m.array[1]).all()
    assert r.params.z_diff is not None and 80 < float(r.params.z_diff.array[0]) < 120
    s = r.summary()
    assert "digest" in s and s["accuracy_class"] == "validated"
    assert any("panels" in n for n in r.notes) and any("truncated" in n for n in r.notes)
    # 20 frequencies with a causal DS dielectric stay within the solve budget
    assert r.xs_key == xs.key()
