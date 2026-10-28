# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Content-addressed artifact store, task-output cache and LRU garbage collection.

Layout under ``<project>/.oa/cache``::

    cas/<sha256[:2]>/<sha256>          artifact blobs
    tasks/<key>/output.json            TaskOutput of a cached task

Last use is tracked with file mtimes (touched on every hit), so ``gc`` can evict least recently
used entries first without a database.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

from oa_framework.analysis import TaskOutput
from oa_jobs._util import canonical_json, sha256_text
from oa_results.artifact import ArtifactRef

__all__ = ["Cache", "CasStore", "GcReport", "TaskCache", "task_key"]


def task_key(
    kind: str,
    version: str,
    payload: object,
    input_shas: list[str],
    board_hash: str,
    physics_hash: str | None,
    tool_versions: dict[str, str | None],
) -> str:
    """Cache key of a task (interfaces.md section 4.4). Independent of dict key order."""
    return sha256_text(
        canonical_json(
            [
                kind,
                version,
                payload,
                sorted(input_shas),
                board_hash,
                physics_hash,
                dict(sorted(tool_versions.items())),
            ]
        )
    )


def _touch(p: Path) -> None:
    with contextlib.suppress(OSError):
        os.utime(p)


class CasStore:
    """``oa_framework.analysis.ArtifactStore`` backed by the shared CAS directory."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def blob_path(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256

    def has(self, sha256: str) -> bool:
        return self.blob_path(sha256).is_file()

    def put_bytes(self, data: bytes, *, name: str, media_type: str) -> ArtifactRef:
        sha = hashlib.sha256(data).hexdigest()
        dest = self.blob_path(sha)
        if dest.is_file():
            _touch(dest)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(f"{sha}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, dest)
        return ArtifactRef(sha256=sha, media_type=media_type, name=name, size=len(data))

    def put_file(self, path: Path, *, name: str, media_type: str) -> ArtifactRef:
        h = hashlib.sha256()
        size = 0
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
                size += len(chunk)
        sha = h.hexdigest()
        dest = self.blob_path(sha)
        if dest.is_file():
            _touch(dest)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(f"{sha}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
            shutil.copyfile(path, tmp)
            os.replace(tmp, dest)
        return ArtifactRef(sha256=sha, media_type=media_type, name=name, size=size)

    def path(self, ref: ArtifactRef) -> Path:
        p = self.blob_path(ref.sha256)
        if not p.is_file():
            raise FileNotFoundError(f"artifact {ref.name!r} ({ref.sha256[:12]}) is not in the CAS")
        _touch(p)
        return p


class TaskCache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def entry(self, key: str) -> Path:
        return self.root / key / "output.json"

    def get(self, key: str, cas: CasStore) -> TaskOutput | None:
        """The cached output, or None when absent or when one of its artifacts was evicted."""
        p = self.entry(key)
        if not p.is_file():
            return None
        try:
            out = TaskOutput.model_validate_json(p.read_text(encoding="utf-8"))
        except ValueError:
            return None
        if not all(cas.has(a.sha256) for a in out.artifacts):
            return None
        _touch(p)
        for a in out.artifacts:
            _touch(cas.blob_path(a.sha256))
        return out

    def put(self, key: str, output: TaskOutput) -> None:
        p = self.entry(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"output.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        tmp.write_text(output.model_dump_json(), encoding="utf-8", newline="\n")
        os.replace(tmp, p)


@dataclass(frozen=True)
class GcReport:
    freed_bytes: int
    removed_tasks: int
    removed_blobs: int
    size_after: int


class Cache:
    """The CAS plus the task cache of one project."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.cas = CasStore(self.root / "cas")
        self.tasks = TaskCache(self.root / "tasks")

    def size(self) -> int:
        return sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file())

    def gc(self, max_size_bytes: int, pinned: set[str] | None = None) -> GcReport:
        """Evict least recently used entries until the cache is at most ``max_size_bytes``.

        Blobs no remaining task entry refers to are removed first (oldest first), then whole task
        entries oldest first. Blobs in ``pinned`` (board artifacts of active jobs, files that
        stored results refer to) are never removed.
        """
        pinned = pinned or set()
        total = self.size()
        freed = n_tasks = n_blobs = 0
        entries: list[tuple[float, Path, list[str]]] = []
        refcount: dict[str, int] = {}
        for d in self.tasks.root.iterdir() if self.tasks.root.is_dir() else ():
            out = d / "output.json"
            if not out.is_file():
                continue
            try:
                shas = [
                    a.sha256
                    for a in TaskOutput.model_validate_json(out.read_text("utf-8")).artifacts
                ]
            except ValueError:
                shas = []
            entries.append((out.stat().st_mtime, d, shas))
            for s in shas:
                refcount[s] = refcount.get(s, 0) + 1
        blobs = sorted(
            (f for f in self.cas.root.rglob("*") if f.is_file() and not f.name.endswith(".tmp")),
            key=lambda f: f.stat().st_mtime,
        )

        def drop_blob(f: Path) -> None:
            nonlocal total, freed, n_blobs
            size = f.stat().st_size
            f.unlink(missing_ok=True)
            total -= size
            freed += size
            n_blobs += 1

        for f in blobs:
            if total <= max_size_bytes:
                break
            if f.name not in pinned and refcount.get(f.name, 0) == 0:
                drop_blob(f)
        entries.sort(key=lambda e: e[0])
        for _, d, shas in entries:
            if total <= max_size_bytes:
                break
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            shutil.rmtree(d, ignore_errors=True)
            total -= size
            freed += size
            n_tasks += 1
            for s in shas:
                refcount[s] -= 1
                f = self.cas.blob_path(s)
                if refcount[s] == 0 and s not in pinned and f.is_file():
                    drop_blob(f)
        return GcReport(freed, n_tasks, n_blobs, max(total, 0))
