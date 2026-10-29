# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Process pool for independent meshing tasks (one gmsh session per worker process).

gmsh keeps global state, so parallelism needs processes. The pool launches plain interpreters
(``python -I -m oa_mesh._worker``) and talks pickle over pipes instead of using
``multiprocessing``: nothing re-imports the caller's ``__main__``, so scripts need no
``if __name__ == "__main__"`` guard and the pool also works from notebooks and ``python -c``.

Results are stored by task index, and each task is a pure function of its arguments (a fresh gmsh
session per task), so the output does not depend on the worker count or the scheduling order.
"""

from __future__ import annotations

import os
import pickle
import queue
import subprocess
import sys
import threading
from collections.abc import Sequence
from typing import Any

from oa_mesh.errors import MeshError

__all__ = ["MAX_WORKERS", "resolve_workers", "run_tasks"]

MAX_WORKERS = 64


def resolve_workers(workers: int | None) -> int:
    """``workers`` or, when None, the ``OA_MESH_WORKERS`` environment variable (default 1)."""
    if workers is None:
        raw = os.environ.get("OA_MESH_WORKERS", "1")
        try:
            workers = int(raw)
        except ValueError:
            raise MeshError(
                f"OA_MESH_WORKERS={raw!r} is not an integer", code="MESH_SETUP", path="workers"
            ) from None
    if not 1 <= workers <= MAX_WORKERS:
        raise MeshError(f"workers must be in 1..{MAX_WORKERS}", code="MESH_SETUP", path="workers")
    return workers


def _spawn() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-I", "-m", "oa_mesh._worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def _raise_reply(kind: str, payload: Any, label: str) -> None:
    if kind == "mesh_error":
        raise MeshError(
            payload["message"],
            code=payload["code"],
            path=payload["path"],
            hint=payload["hint"],
            details=payload["details"],
        )
    raise MeshError(
        f"worker failed on {label}: {str(payload).strip().splitlines()[-1]}",
        code="MESH_GMSH_FAILED",
        hint="rerun with workers=1 to get the full traceback",
        details={"traceback": str(payload)},
    )


def run_tasks(name: str, tasks: Sequence[tuple[Any, ...]], workers: int) -> list[Any]:
    """Run ``tasks`` through ``workers`` processes; results are in task order.

    The first failing task (lowest index) raises its ``MeshError``; later tasks may still run.
    """
    n = min(workers, len(tasks))
    results: list[Any] = [None] * len(tasks)
    errors: dict[int, tuple[str, Any]] = {}
    todo: queue.Queue[int] = queue.Queue()
    for i in range(len(tasks)):
        todo.put(i)
    procs: list[subprocess.Popen[bytes]] = []

    def drive() -> None:
        proc = _spawn()
        procs.append(proc)
        assert proc.stdin is not None and proc.stdout is not None
        try:
            while True:
                try:
                    i = todo.get_nowait()
                except queue.Empty:
                    break
                try:
                    pickle.dump((name, tasks[i]), proc.stdin, protocol=pickle.HIGHEST_PROTOCOL)
                    proc.stdin.flush()
                    kind, payload = pickle.load(proc.stdout)
                except (EOFError, BrokenPipeError, OSError):
                    errors[i] = ("exc", "worker process died (out of memory?)")
                    break
                if kind == "ok":
                    results[i] = payload
                else:
                    errors[i] = (kind, payload)
            try:
                pickle.dump(None, proc.stdin)
                proc.stdin.flush()
            except OSError:
                pass
        finally:
            try:
                proc.stdin.close()
                proc.stdout.close()
            finally:
                proc.wait(timeout=30)

    threads = [threading.Thread(target=drive, daemon=True) for _ in range(n)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    except BaseException:
        for p in procs:
            p.kill()
        raise
    if errors:
        i = min(errors)
        _raise_reply(*errors[i], label=f"task {i}")
    return results
