# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``write_bundle`` / ``read_bundle`` (interfaces.md section 4.2).

Layout of a result directory::

    result.json        manifest: the bundle with arrays replaced by {"$array": "a/<id>.npy", ...}
    a/<id>.npy         numpy arrays (exact dtype, so complex128 data round-trips bit-exactly)
    t/<sha256>.parquet table artifacts (TableResult.rows)
    x/<sha256>-<name>  other artifacts (exports, meshes, Touchstone files)

``result.json`` is written last and atomically, so a directory without it is incomplete.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from oa_model.arrays import Floats, NDArray, Points
from oa_results.artifact import ArtifactRef
from oa_results.bundle import ResultBundle
from oa_results.io.tables import PARQUET_MEDIA_TYPE

__all__ = ["MANIFEST", "bundle_artifact_path", "read_bundle", "write_bundle"]

MANIFEST = "result.json"
_ART_KEYS = {"sha256", "media_type", "name", "size"}
_ARRAY_TYPES: dict[str, type[Any]] = {"NDArray": NDArray, "Floats": Floats, "Points": Points}


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:80] or "artifact"


def bundle_artifact_path(directory: str | Path, ref: ArtifactRef) -> Path:
    """Where ``write_bundle`` placed the file of ``ref`` inside ``directory``."""
    d = Path(directory)
    if ref.media_type == PARQUET_MEDIA_TYPE:
        return d / "t" / f"{ref.sha256}.parquet"
    return d / "x" / f"{ref.sha256}-{_safe(ref.name)}"


class _Encoder:
    def __init__(self, directory: Path, artifacts: Callable[[ArtifactRef], Path] | None) -> None:
        self.dir = directory
        self.artifacts = artifacts

    def array(self, obj: Any) -> dict[str, str]:
        arr = np.ascontiguousarray(obj.array)
        tag = type(obj).__name__
        h = hashlib.sha256(f"{tag}{arr.dtype.str}{arr.shape}".encode() + arr.tobytes())
        rel = f"a/{h.hexdigest()[:32]}.npy"
        target = self.dir / rel
        if not target.exists():
            np.save(target, arr, allow_pickle=False)
        return {"$array": rel, "type": tag}

    def encode(self, obj: Any) -> Any:
        if isinstance(obj, NDArray | Floats | Points):
            return self.array(obj)
        if isinstance(obj, dict):
            if set(obj) == _ART_KEYS:
                self.artifact(ArtifactRef.model_validate(obj))
            return {str(k): self.encode(v) for k, v in obj.items()}
        if isinstance(obj, tuple | list):
            return [self.encode(v) for v in obj]
        return obj

    def artifact(self, ref: ArtifactRef) -> None:
        if self.artifacts is None:
            return
        dest = bundle_artifact_path(self.dir, ref)
        if not dest.exists():
            shutil.copyfile(self.artifacts(ref), dest)


def write_bundle(
    bundle: ResultBundle,
    directory: str | Path,
    artifacts: Callable[[ArtifactRef], Path] | None = None,
) -> Path:
    """Write ``bundle`` into ``directory`` (created). Returns the manifest path.

    ``artifacts`` resolves an ``ArtifactRef`` to a local file (normally the job CAS); referenced
    files are copied into ``t/`` and ``x/`` so the directory is self-contained. Without it only
    the references are recorded.
    """
    d = Path(directory)
    for sub in ("a", "t", "x"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    doc = _Encoder(d, artifacts).encode(bundle.model_dump(mode="python", by_alias=True))
    tmp = d / (MANIFEST + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False)
        fh.write("\n")
    os.replace(tmp, d / MANIFEST)
    return d / MANIFEST


def _decode(obj: Any, directory: Path) -> Any:
    if isinstance(obj, dict):
        if "$array" in obj:
            arr = np.load(directory / obj["$array"], allow_pickle=False)
            cls = _ARRAY_TYPES[obj["type"]]
            if cls is NDArray:
                return NDArray(arr, dtype={"f": "f8", "c": "c16", "i": "i8"}[arr.dtype.kind])
            return cls(arr)
        return {k: _decode(v, directory) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decode(v, directory) for v in obj]
    return obj


def read_bundle(directory: str | Path) -> ResultBundle:
    """Read a bundle written by :func:`write_bundle`."""
    d = Path(directory)
    with open(d / MANIFEST, encoding="utf-8") as fh:
        doc = json.load(fh)
    return ResultBundle.model_validate(_decode(doc, d))
