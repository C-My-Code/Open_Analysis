# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import sqlite3
from pathlib import Path

from oa_framework.analysis import TaskOutput
from oa_jobs import Cache, JobStore, task_key
from oa_jobs.models import Budget, clamp_budget


def _mkjob(store: JobStore, job_id: str = "J1") -> None:
    store.create_job(
        job_id=job_id,
        analysis="a",
        setup="s",
        priority=0,
        request={},
        setup_doc={},
        meta={},
        caller="test",
        tasks=[("t1", "k1", "{}", 1.0), ("t2", "k2", "{}", 1.0)],
    )


def test_store_is_wal_and_events_page_without_loss(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite")
    mode = sqlite3.connect(tmp_path / "jobs.sqlite").execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    _mkjob(store)
    n = 10_000
    for start in range(0, n, 1000):
        store.append_events(
            "J1", [("log", "t1", {"i": i}) for i in range(start, min(n, start + 1000))]
        )
    seen: list[int] = []
    after = 0
    while True:
        rows = store.events("J1", after, 777)  # deliberately not a divisor of n
        if not rows:
            break
        seen += [r["seq"] for r in rows]
        after = rows[-1]["seq"]
    assert seen == list(range(1, n + 1))
    store.close()


def test_requeue_running_after_restart(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite")
    _mkjob(store)
    store.update_task("J1", "t1", state="running", pid=123)
    store.update_task("J1", "t2", state="succeeded")
    store.close()
    again = JobStore(tmp_path / "jobs.sqlite")
    assert again.requeue_running("J1") == 1
    states = {r["task_id"]: r["state"] for r in again.tasks("J1")}
    assert states == {"t1": "pending", "t2": "succeeded"}
    again.close()


def test_task_key_ignores_payload_key_order() -> None:
    a = task_key("k", "1", {"x": 1, "y": {"b": 2, "a": [1, 2]}}, ["s2", "s1"], "bh", None, {})
    b = task_key("k", "1", {"y": {"a": [1, 2], "b": 2}, "x": 1}, ["s1", "s2"], "bh", None, {})
    c = task_key("k", "2", {"x": 1, "y": {"b": 2, "a": [1, 2]}}, ["s2", "s1"], "bh", None, {})
    d = task_key("k", "1", {"x": 1, "y": {"b": 2, "a": [1, 3]}}, ["s2", "s1"], "bh", None, {})
    assert a == b
    assert len({a, c, d}) == 3


def test_cas_dedupes_and_task_cache_validates_artifacts(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache")
    r1 = cache.cas.put_bytes(b"abc", name="a", media_type="text/plain")
    r2 = cache.cas.put_bytes(b"abc", name="other", media_type="text/plain")
    assert r1.sha256 == r2.sha256
    assert cache.cas.path(r1).read_bytes() == b"abc"
    out = TaskOutput(task_id="t", data={"v": 1}, artifacts=(r1,))
    cache.tasks.put("key1", out)
    assert cache.tasks.get("key1", cache.cas) == out
    cache.cas.blob_path(r1.sha256).unlink()
    assert cache.tasks.get("key1", cache.cas) is None  # evicted blob invalidates the entry
    assert cache.tasks.get("missing", cache.cas) is None


def test_gc_evicts_lru_first_and_keeps_pinned(tmp_path: Path) -> None:
    import os
    import time

    cache = Cache(tmp_path / "cache")
    refs = []
    for i in range(4):
        ref = cache.cas.put_bytes(bytes([i]) * 1000, name=f"b{i}", media_type="x/y")
        refs.append(ref)
        cache.tasks.put(f"k{i}", TaskOutput(task_id=f"t{i}", artifacts=(ref,)))
        old = time.time() - (100 - i * 10)  # k0 oldest
        os.utime(cache.tasks.entry(f"k{i}"), (old, old))
        os.utime(cache.cas.blob_path(ref.sha256), (old, old))
    total = cache.size()
    report = cache.gc(total - 1500, pinned={refs[0].sha256})
    assert report.removed_tasks >= 1
    assert cache.cas.has(refs[0].sha256)  # pinned blob survives although its task was evicted
    assert not cache.tasks.entry("k0").exists()
    assert cache.tasks.entry("k3").exists()
    assert cache.size() <= total - 1500 + 1000


def test_clamp_budget_takes_the_smaller_limit() -> None:
    eff = clamp_budget(
        Budget(max_runtime_s=600, max_memory_gb=64, allow_external=True),
        Budget(max_runtime_s=3600, max_memory_gb=32, max_cpus=4, allow_external=False),
    )
    assert (eff.max_runtime_s, eff.max_memory_gb, eff.max_cpus) == (600, 32, 4)
    assert eff.allow_external is False
