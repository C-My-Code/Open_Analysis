# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Project file watching: reload a project when its board, sidecars or setups change.

Polling on (mtime, size) signatures, so it works on every OS without extra dependencies. A change
must be stable for ``debounce`` seconds before the project is reloaded (editors write in bursts).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = ["ProjectWatcher", "signature"]

log = logging.getLogger("oa.server.watch")


def signature(paths: tuple[Path, ...]) -> tuple[tuple[str, int, int], ...]:
    """(path, mtime_ns, size) of every file under ``paths`` (directories are walked)."""
    out = []
    for p in paths:
        files = sorted(p.rglob("*")) if p.is_dir() else [p]
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            if f.is_file():
                out.append((str(f), st.st_mtime_ns, st.st_size))
    return tuple(out)


class ProjectWatcher:
    """Background thread that reloads changed projects of ``service``."""

    def __init__(
        self,
        service: Any,
        *,
        interval: float = 0.25,
        debounce: float = 0.3,
        on_reload: Callable[[str], None] | None = None,
    ) -> None:
        self.service = service
        self.interval = interval
        self.debounce = debounce
        self.on_reload = on_reload
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seen: dict[str, tuple[Any, ...]] = {}
        self._pending: dict[str, tuple[tuple[Any, ...], float]] = {}

    def poll(self, now: float | None = None) -> list[str]:
        """One polling step; returns the ids reloaded in this step."""
        now = time.monotonic() if now is None else now
        reloaded = []
        for h in list(self.service.projects()):
            pid = h.project_id
            paths = tuple(getattr(self.service.project(pid), "watch_paths", ()))
            sig = signature(paths)
            if pid not in self._seen:
                self._seen[pid] = sig
                continue
            if sig == self._seen[pid]:
                self._pending.pop(pid, None)
                continue
            prev = self._pending.get(pid)
            if prev is None or prev[0] != sig:
                self._pending[pid] = (sig, now)  # still changing: restart the debounce
                continue
            if now - prev[1] >= self.debounce:
                self._pending.pop(pid, None)
                self._seen[pid] = sig
                try:
                    self.service.reload(pid)
                    reloaded.append(pid)
                    if self.on_reload:
                        self.on_reload(pid)
                except Exception:  # a half-written file must not kill the watcher
                    log.exception("reload of %s failed", pid)
        return reloaded

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self.poll()

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="oa-watch", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
