# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``impedance.profile`` acceptance (WP-203) on the WP-102 boards and synthetic nets."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Sequence
from typing import Any

import numpy as np
from profile_helpers import expected, load_board, profile_of, run_profile, table

from oa_impedance import closed_form as cf
from oa_impedance.profile import ImpedanceProfile, ImpedanceProfileResult
from oa_impedance.service import ImpedanceServiceImpl
from oa_model.copper import Track
from oa_model.geometry import LineSeg
from oa_model.xsection import CrossSection
from oa_results.bundle import ArtifactOnly, ResultBundle
from oa_results.container import as_np
from oa_testkit import builders
from oa_testkit.golden import assert_close_to_golden

C0 = 299792458.0
T_CU = 35e-6


def _q(p: Any, name: str) -> np.ndarray[Any, Any]:
    return as_np(next(q.values for q in p.quantities if q.name == name))


def test_microstrip_flat_and_one_key_per_width() -> None:
    b = load_board("microstrip")
    run = run_profile(b, ("MS_*",))
    bundle = run.bundle
    exp = expected("microstrip")["geometry"]["microstrip"]
    h, er = exp["h_to_reference"], exp["dk"]
    zs = {}
    for tr in exp["traces"]:
        p = profile_of(bundle, tr["net"])
        z = _q(p, "Z0")
        assert float(z.max() - z.min()) < 0.1
        hj = cf.microstrip_hj(tr["width"], h, exp["copper_thickness"], er).z0
        assert hj is not None
        assert abs(float(z[0]) - hj) / hj < 1e-3
        assert abs(float(z[0]) - tr["z0_ohm_approx"]) < 0.1
        assert not p.events
        zs[tr["net"]] = float(z[0])
    assert_close_to_golden(zs, "impedance_profile", "microstrip_hj")
    rows = table(bundle, "deviation")
    assert [r["n_sections"] for r in rows] == [1, 1, 1]
    assert len(table(bundle, "sections")) == 3
    # one solve task per batch of unique keys, plus the layout task
    assert [t.id.split(":")[0] for t in run.plan.tasks] == ["layout", "solve"]


def test_diff_pair_uncoupled_breakout_event() -> None:
    b = load_board("diff_pair")
    bundle = run_profile(b).bundle
    p = profile_of(bundle, "DP_P/DP_N")
    assert p.target == 100.0 and isinstance(p, ImpedanceProfileResult)
    exp = expected("diff_pair")["geometry"]["diff_pair"]
    ev = [e for e in p.events if "pair_coupled" in e.message]
    assert ev, p.events
    assert abs(ev[0].s - exp["breakout"]["uncoupled_length"]) < 0.1e-3
    # uncoupled leg: two independent lines, Zdiff = 2 Z0, clearly off the coupled value
    z = _q(p, "Zdiff")
    assert abs(float(z[0]) - float(z[1])) / float(z[1]) > 0.1
    sv = as_np(p.s)
    ln = np.where(sv < 0.039, np.diff(sv, append=sv[-1]), 0.0)
    mid = float(z[int(np.argmax(ln))])
    mic = exp["microstrip"]
    odd = cf.edge_coupled_microstrip(
        mic["width"], mic["gap"], mic["h_to_reference"], 35e-6, 4.3
    ).z0o
    assert odd is not None
    assert abs(mid - 2 * odd) / (2 * odd) < 0.01
    segs = table(bundle, "discontinuities")
    assert segs[0]["causes"] == "pair_uncoupled" and segs[0]["out_of_tolerance"]


def test_split_plane_event_at_slot() -> None:
    b = load_board("combined")
    bundle = run_profile(b, ("SIG_SLOT", "SIG_REFCHG")).bundle
    p = profile_of(bundle, "SIG_SLOT")
    slot = expected("combined")["geometry"]["split_plane"]["slot"]["x_range_mm"]
    trk = next(t for t in b.tracks if t.net == "SIG_SLOT")
    x0 = trk.path[0].start[0]  # type: ignore[union-attr]
    xs = [x0 + e.s for e in p.events]
    for edge in (slot[0] * 1e-3, slot[1] * 1e-3):
        assert any(abs(x - edge) < 50e-6 for x in xs), (edge, xs)
    assert {e.kind for e in p.events} <= {"ref_change", "other", "plane_edge"}
    z = _q(p, "Z0")
    assert z.max() > 1.5 * z.min()  # the reference gap is a large impedance step
    rc = profile_of(bundle, "SIG_REFCHG")
    assert any(
        e.kind == "ref_change" and abs(x - 0.075) < 0.3e-3 for e in rc.events for x in [e.s + 0.060]
    )


def test_stripline_delay_is_sqrt_er_over_c() -> None:
    b = load_board("stripline")
    bundle = run_profile(b, ("SL_SYM",)).bundle
    p = profile_of(bundle, "SL_SYM")
    d = _q(p, "delay_per_m")
    s = as_np(p.s)
    longest = int(np.argmax(np.diff(s, append=s[-1])))
    assert abs(float(d[longest]) - math.sqrt(4.3) / C0) / (math.sqrt(4.3) / C0) < 5e-3
    assert_close_to_golden(
        {"delay_s_per_m": float(d[longest])}, "impedance_profile", "stripline_delay"
    )


class _Counting(ImpedanceServiceImpl):
    def __init__(self) -> None:
        super().__init__()
        self.solved = 0
        self.calls = 0

    def solve_many(
        self,
        xss: Sequence[CrossSection],
        freqs: Sequence[float] | None = None,
        *,
        method: str = "auto",
    ) -> list[Any]:
        self.calls += 1
        self.solved += len(xss)
        return super().solve_many(xss, freqs, method=method)

    def solve(
        self, xs: CrossSection, freqs: Sequence[float] | None = None, *, method: str = "auto"
    ) -> Any:  # type: ignore[override]
        self.solved += 1
        return super().solve(xs, freqs, method=method)  # type: ignore[arg-type]


def test_500_segments_three_sections_three_solves() -> None:
    length = 100e-3
    b = builders.microstrip_board(length=length)
    widths = (0.3e-3, 0.38e-3, 0.45e-3)
    n = 500
    step = length / n
    tracks = tuple(
        Track(
            id=f"t{i}",
            net="SIG",
            layer="L1",
            width=widths[(i // 50) % 3],
            path=(LineSeg(start=(i * step, 0.0), end=((i + 1) * step, 0.0)),),
        )
        for i in range(n)
    )
    b = b.model_copy(update={"tracks": tracks})
    svc = _Counting()
    run = run_profile(b, ("SIG",), services={"impedance": svc}, ds=1e-3)
    assert svc.solved == 3
    rows = table(run.bundle, "sections")
    assert len(rows) == 3
    assert len(run.plan.tasks) == 2
    assert svc.calls == 1


def test_summary_within_2kib_for_100_nets() -> None:
    b = load_board("microstrip")
    bundle = run_profile(b, ("MS_*",)).bundle
    item = next(i for i in bundle.items if i.id == "profile:MS_250")
    one = item.data.summary(max_chars=2048)
    assert len(json.dumps(one)) <= 2048
    stats = one["stats"]
    assert isinstance(stats, dict)
    for k in ("z_min", "z_max", "z_mean", "target", "oot_m", "worst_event"):
        assert k in stats
    many = []
    for i in range(100):
        many.append(item.model_copy(update={"id": f"profile:N{i:03d}"}))
    big = bundle.model_copy(update={"items": (*many, *bundle.items[3:])})
    assert len(json.dumps(big.summary(max_chars=2048))) <= 2048
    assert len(json.dumps(item.data.summary())) <= 2048


def test_accuracy_and_roundtrip() -> None:
    b = load_board("microstrip")
    bundle = run_profile(b, ("MS_*",)).bundle
    for it in bundle.items:
        assert it.data.accuracy_class is not None
    p = profile_of(bundle, "MS_350")
    assert p.accuracy_class == "screening"
    flag = next(f for f in p.validity_flags if f.code == "accuracy")
    assert "accuracy_class=closed_form" in flag.message
    again = ResultBundle.model_validate_json(bundle.model_dump_json(by_alias=True))
    assert [i.id for i in again.items] == [i.id for i in bundle.items]


def test_tuning_profile_data_structure() -> None:
    b = load_board("microstrip")
    run = run_profile(b, ("MS_*",))
    art = next(i for i in run.bundle.items if i.id == "tuning_profile")
    assert isinstance(art.data, ArtifactOnly)
    doc = json.loads(run.artifacts.get(art.data.artifact))
    assert doc["schema_id"] == "oa-tuning-profile/1"
    prof = doc["profiles"][0]
    assert len(doc["profiles"]) == 1
    (layer,) = prof["layers"]
    assert layer["layer"] == "L1"
    assert 5.5 < layer["delay_ps_per_mm"] < 7.5
    rows = table(run.bundle, "layer_delays")
    assert rows[0]["layer"] == "L1"


def test_combined_board_cache_stats_and_time() -> None:
    b = load_board("combined")
    t0 = time.perf_counter()
    run = run_profile(b)
    dt = time.perf_counter() - t0
    bundle = run.bundle
    stats = {s.name: s.value for s in next(i for i in bundle.items if i.id == "stats").data.values}  # type: ignore[union-attr]
    runs = stats["samples_runs"]
    assert stats["unique_sections"] < runs
    rows = {r["net"]: r for r in table(bundle, "deviation")}
    assert rows["MS_250"]["target"] == 50.0 and rows["MS_250"]["status"] == "fail"
    assert rows["MS_350"]["status"] == "pass"
    assert rows["MS_500"]["status"] == "fail"
    assert rows["USB_D+/USB_D-"]["target"] == 90.0
    assert rows["DP_P/DP_N"]["target"] == 100.0
    assert dt < 120
    print(f"combined: {stats['unique_sections']:.0f} unique sections, {runs:.0f} runs, {dt:.1f} s")


def test_setup_validation_and_analysis_protocol() -> None:
    from oa_framework.analysis import Analysis

    assert isinstance(ImpedanceProfile(), Analysis)
