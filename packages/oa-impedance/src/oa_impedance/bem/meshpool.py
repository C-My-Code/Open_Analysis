# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Meshing of large BEM batches in worker processes (WP-519).

``build_problem`` is about 10 ms to 30 ms of pure Python and shapely calls per section and holds
the GIL, so the threads of ``solve_many`` cannot overlap it: for 383 sections it is the longest
stage of the batch once the dense solves run in parallel. A pool of worker processes does it
instead. Each worker is started with ``subprocess`` (not ``multiprocessing``), so no ``__main__``
module is re-imported in the children, whatever script calls the library.

``mesh_many`` returns one future per section; the future holds the ``Problem`` or ``None`` when
the worker could not mesh the section (the caller then meshes it in process and meets the same
error). The mesh is a pure function of the section, so results do not depend on the worker.
Controls: ``OA_IMPEDANCE_MESH_WORKERS`` (0 disables; default half of ``OA_NUM_THREADS`` or of
the CPU count, at most 8) and ``OA_IMPEDANCE_MESH_MIN`` (smallest batch that uses the pool,
default 48).
"""

from __future__ import annotations

import atexit
import contextlib
import os
import queue
import subprocess
import sys
import threading
from collections.abc import Sequence
from concurrent.futures import Future
from typing import Any

from oa_model.xsection import CrossSection

from .geometry import MeshParams, Problem
from .meshworker import read_frame, write_frame

__all__ = ["MeshPool", "mesh_many", "pool_size", "shutdown"]

_DEFAULT_MIN = 48
_IDLE_S = 60.0  # a worker with no job for this long exits
_POOL: MeshPool | None = None
_POOL_LOCK = threading.Lock()


def pool_size() -> int:
    """Worker processes the pool would use (0: disabled)."""
    raw = os.environ.get("OA_IMPEDANCE_MESH_WORKERS", "").strip()
    try:
        if raw:
            return max(0, min(int(raw), 32))
        threads = int(os.environ.get("OA_NUM_THREADS", "0") or 0)
    except ValueError:
        return 0
    cores = threads if threads > 0 else min(os.cpu_count() or 1, 8)
    return max(1, min(cores, 8) // 2)  # the solve threads of the batch use the other half


def min_batch() -> int:
    try:
        return max(1, int(os.environ.get("OA_IMPEDANCE_MESH_MIN", str(_DEFAULT_MIN))))
    except ValueError:
        return _DEFAULT_MIN


class _Worker:
    def __init__(self, jobs: queue.Queue[Any], on_exit: Any) -> None:
        env = dict(os.environ)
        for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OA_NUM_THREADS"):
            env[key] = "1"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "oa_impedance.bem.meshworker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=flags,
        )
        self._jobs = jobs
        self._on_exit = on_exit
        self.thread = threading.Thread(target=self._serve, name="oa-mesh-worker", daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        proc = self.proc
        assert proc.stdin is not None and proc.stdout is not None
        job: Any = None
        try:
            if read_frame(proc.stdout) != ("ready",):
                raise OSError("mesh worker did not start")
            while True:
                try:
                    job = self._jobs.get(timeout=_IDLE_S)
                except queue.Empty:
                    return  # idle: give the memory back, the next batch starts a new pool
                if job is None:
                    return
                jid, xs, params, fut = job
                if not fut.set_running_or_notify_cancel():
                    continue
                write_frame(proc.stdin, (jid, xs, params))
                reply = read_frame(proc.stdout)
                if reply is None:
                    raise OSError("mesh worker exited")
                fut.set_result(reply[2] if reply[1] else None)
                job = None
        except Exception as exc:  # worker died or the pipe broke: the batch continues in process
            if job is not None and not job[3].done():
                job[3].set_exception(exc)
        finally:
            self._on_exit(self)
            with contextlib.suppress(OSError):
                proc.stdin.close()
            proc.kill()
            proc.wait()
            with contextlib.suppress(OSError):
                proc.stdout.close()


class MeshPool:
    """``n`` worker processes meshing sections taken from one queue."""

    def __init__(self, n: int) -> None:
        self._jobs: queue.Queue[Any] = queue.Queue()
        self._lock = threading.Lock()
        self._live = 0
        self._next = 0
        self.workers: list[_Worker] = []
        for _ in range(n):
            self._live += 1
            self.workers.append(_Worker(self._jobs, self._worker_gone))

    def _worker_gone(self, _: _Worker) -> None:
        with self._lock:
            self._live -= 1
            dead = self._live <= 0
        if dead:
            self._drain()

    def _drain(self) -> None:
        """Nobody is left to serve the queue: release its waiting futures."""
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                return
            if job is not None and not job[3].done():
                job[3].set_exception(OSError("mesh pool is down"))

    @property
    def alive(self) -> bool:
        return self._live > 0

    def submit(self, xs: CrossSection, params: MeshParams) -> Future[Problem | None]:
        fut: Future[Problem | None] = Future()
        with self._lock:
            self._next += 1
            jid = self._next
        self._jobs.put((jid, xs, params, fut))
        if not self.alive:  # raced with the last worker's exit
            self._drain()
        return fut

    def close(self) -> None:
        for _ in self.workers:
            self._jobs.put(None)
        for w in self.workers:
            w.thread.join(timeout=2.0)
            if w.proc.poll() is None:
                w.proc.kill()


def _pool() -> MeshPool | None:
    global _POOL
    n = pool_size()
    if n <= 0:
        return None  # disabled now, even if a pool started earlier is still idling out
    with _POOL_LOCK:
        if _POOL is not None and not _POOL.alive:
            _POOL = None
        if _POOL is None:
            try:
                _POOL = MeshPool(n)
            except OSError:
                return None
            atexit.register(shutdown)
        return _POOL


def shutdown() -> None:
    """Stop the workers (also run at interpreter exit)."""
    global _POOL
    with _POOL_LOCK:
        pool, _POOL = _POOL, None
    if pool is not None:
        pool.close()


def mesh_many(
    sections: Sequence[CrossSection], params: MeshParams
) -> list[Future[Problem | None]] | None:
    """One future per section (in order), or ``None`` when the batch should be meshed in process
    (too small, pool disabled or not startable)."""
    if len(sections) < min_batch():
        return None
    pool = _pool()
    if pool is None:
        return None
    return [pool.submit(xs, params) for xs in sections]
