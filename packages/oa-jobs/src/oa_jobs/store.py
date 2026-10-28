# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""SQLite (WAL) job store: ``jobs``, ``tasks`` and ``events`` (interfaces.md section 4.4).

One connection per thread, autocommit with explicit ``BEGIN IMMEDIATE`` for multi-statement
writes, so worker processes and the coordinator can share the file. Event sequence numbers are
assigned inside the write transaction (``MAX(seq)+1`` per job), so they are gapless and
monotonic even with concurrent writers.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from oa_jobs._util import utc_now

__all__ = ["ACTIVE_STATES", "TERMINAL_STATES", "JobStore"]

TERMINAL_STATES = ("succeeded", "failed", "cancelled")
ACTIVE_STATES = ("queued", "planning", "running", "reducing")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    analysis TEXT NOT NULL,
    setup TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    request_json TEXT NOT NULL,
    setup_json TEXT NOT NULL,
    meta_json TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    result_id TEXT,
    error_json TEXT,
    cancel_reason TEXT,
    caller TEXT,
    elapsed_s REAL NOT NULL DEFAULT 0,
    peak_memory_gb REAL NOT NULL DEFAULT 0,
    created_utc TEXT NOT NULL,
    updated_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    job_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    state TEXT NOT NULL,
    key TEXT NOT NULL,
    task_json TEXT NOT NULL,
    est_seconds REAL NOT NULL DEFAULT 1,
    cached INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    pid INTEGER,
    output_json TEXT,
    error_json TEXT,
    started_utc TEXT,
    finished_utc TEXT,
    PRIMARY KEY (job_id, task_id)
);
CREATE TABLE IF NOT EXISTS events (
    job_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    task_id TEXT,
    data_json TEXT NOT NULL,
    utc TEXT NOT NULL,
    PRIMARY KEY (job_id, seq)
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs (state, created_utc);
"""


class JobStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._cond = threading.Condition()
        self._conns: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        c = self._conn()
        c.executescript(_SCHEMA)

    # -- connections ----------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        c: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(
                self.path, timeout=60, isolation_level=None, check_same_thread=False
            )
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA busy_timeout=60000")
            self._local.conn = c
            with self._lock:
                self._conns.append(c)
        return c

    def close(self) -> None:
        with self._lock:
            for c in self._conns:
                with contextlib.suppress(sqlite3.Error):
                    c.close()
            self._conns.clear()
        self._local = threading.local()

    def _write(self, sql: str, args: Sequence[Any] = ()) -> None:
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            c.execute(sql, args)
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise

    # -- jobs -----------------------------------------------------------------------------

    def create_job(
        self,
        *,
        job_id: str,
        analysis: str,
        setup: str,
        priority: int,
        request: dict[str, Any],
        setup_doc: dict[str, Any],
        meta: dict[str, Any],
        caller: str,
        tasks: Iterable[tuple[str, str, str, float]],  # (task_id, key, task_json, est_seconds)
    ) -> None:
        now = utc_now()
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            c.execute(
                "INSERT INTO jobs (id, state, analysis, setup, priority, request_json,"
                " setup_json, meta_json, caller, created_utc, updated_utc)"
                " VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    analysis,
                    setup,
                    priority,
                    json.dumps(request),
                    json.dumps(setup_doc),
                    json.dumps(meta),
                    caller,
                    now,
                    now,
                ),
            )
            c.executemany(
                "INSERT INTO tasks (job_id, task_id, state, key, task_json, est_seconds)"
                " VALUES (?, ?, 'pending', ?, ?, ?)",
                [(job_id, tid, key, tj, est) for tid, key, tj, est in tasks],
            )
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_utc"] = utc_now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._write(f"UPDATE jobs SET {cols} WHERE id = ?", [*fields.values(), job_id])
        self._notify()

    def job(self, job_id: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = (
            self._conn().execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        )
        return row

    def jobs(self, state: str | None = None, limit: int = 100) -> list[sqlite3.Row]:
        if state is None:
            q = "SELECT * FROM jobs ORDER BY created_utc DESC, id DESC LIMIT ?"
            a: tuple[Any, ...] = (limit,)
        else:
            q = "SELECT * FROM jobs WHERE state = ? ORDER BY created_utc DESC, id DESC LIMIT ?"
            a = (state, limit)
        return list(self._conn().execute(q, a).fetchall())

    def active_job_ids(self) -> list[str]:
        marks = ",".join("?" for _ in ACTIVE_STATES)
        rows = self._conn().execute(
            f"SELECT id FROM jobs WHERE state IN ({marks}) ORDER BY priority DESC, created_utc",
            ACTIVE_STATES,
        )
        return [r["id"] for r in rows]

    # -- tasks ----------------------------------------------------------------------------

    def tasks(self, job_id: str) -> list[sqlite3.Row]:
        return list(
            self._conn()
            .execute("SELECT * FROM tasks WHERE job_id = ? ORDER BY rowid", (job_id,))
            .fetchall()
        )

    def update_task(self, job_id: str, task_id: str, **fields: Any) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._write(
            f"UPDATE tasks SET {cols} WHERE job_id = ? AND task_id = ?",
            [*fields.values(), job_id, task_id],
        )

    def requeue_running(self, job_id: str) -> int:
        """Tasks left ``running`` by a crashed service go back to ``pending``."""
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            n = c.execute(
                "UPDATE tasks SET state='pending', pid=NULL WHERE job_id=? AND state='running'",
                (job_id,),
            ).rowcount
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        return int(n)

    def counts(self, job_id: str) -> dict[str, float]:
        r = (
            self._conn()
            .execute(
                "SELECT COUNT(*) AS total,"
                " SUM(state IN ('succeeded','cached')) AS done,"
                " SUM(state='cached') AS cached,"
                " SUM(state='failed') AS failed,"
                " COALESCE(SUM(est_seconds), 0) AS est_total,"
                " COALESCE(SUM(CASE WHEN state IN ('succeeded','cached') THEN est_seconds END), 0)"
                " AS est_done FROM tasks WHERE job_id = ?",
                (job_id,),
            )
            .fetchone()
        )
        return {k: float(r[k] or 0) for k in r.keys()}  # noqa: SIM118

    def running_pids(self, job_id: str) -> list[int]:
        rows = self._conn().execute(
            "SELECT pid FROM tasks WHERE job_id=? AND state='running' AND pid IS NOT NULL",
            (job_id,),
        )
        return [int(r["pid"]) for r in rows]

    # -- events ---------------------------------------------------------------------------

    def append_events(
        self, job_id: str, events: Iterable[tuple[str, str | None, dict[str, Any]]]
    ) -> int:
        """Append ``(type, task_id, data)`` events; returns the last sequence number."""
        batch = list(events)
        if not batch:
            return 0
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            seq = c.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM events WHERE job_id = ?", (job_id,)
            ).fetchone()[0]
            now = utc_now()
            rows = []
            for typ, task_id, data in batch:
                seq += 1
                rows.append((job_id, seq, typ, task_id, json.dumps(data), now))
            c.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", rows)
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        self._notify()
        return int(seq)

    def append_event(
        self, job_id: str, typ: str, data: dict[str, Any], task_id: str | None = None
    ) -> int:
        return self.append_events(job_id, [(typ, task_id, data)])

    def events(self, job_id: str, after_seq: int = 0, limit: int = 1000) -> list[sqlite3.Row]:
        return list(
            self._conn()
            .execute(
                "SELECT * FROM events WHERE job_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (job_id, after_seq, limit),
            )
            .fetchall()
        )

    # -- change notification (in-process; other processes are picked up by polling) --------

    def _notify(self) -> None:
        with self._cond:
            self._cond.notify_all()

    def wait_for_change(self, timeout: float) -> None:
        with self._cond:
            self._cond.wait(timeout)
