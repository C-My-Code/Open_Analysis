# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Process-local gmsh lifecycle with deterministic options.

gmsh keeps one global model/option state per process, so:

* ``gmsh_session()`` takes a process-wide lock: threads are serialized (safe, but no speed-up).
  Real parallelism needs separate worker processes.
* The session is not re-entrant: nesting raises ``MeshError`` (``MESH_SESSION``) instead of
  deadlocking.
* gmsh is initialized and finalized inside each session, so a forked child never inherits a live
  gmsh state. Prefer the ``spawn`` start method anyway.
* Signal handlers are never installed (``interruptible=False``): that is what lets gmsh be
  initialized off the main thread.
* gmsh is imported lazily, so ``import oa_mesh`` (types only) never loads or initializes it.
* If other code in the process has already initialized gmsh, the session refuses to touch it
  (``MESH_SESSION``).

Determinism: single thread, fixed ``Mesh.RandomSeed``, Frontal-Delaunay 2D (``Mesh.Algorithm=6``)
and HXT 3D (``Mesh.Algorithm3D=10``). Outputs are reproducible for a given gmsh version.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from oa_mesh.errors import MeshError

__all__ = ["gmsh_session", "gmsh_version"]

_LOCK = threading.Lock()
_STATE = threading.local()

RANDOM_SEED = 1


def gmsh_version() -> str:
    """The installed gmsh version (loads the gmsh module, does not initialize it)."""
    import gmsh  # type: ignore[import-untyped]

    return str(gmsh.__version__)


@contextmanager
def gmsh_session(*, verbose: bool = False) -> Iterator[Any]:
    """Initialize gmsh with deterministic options, yield the ``gmsh`` module, finalize on exit."""
    if getattr(_STATE, "active", False):
        raise MeshError(
            "gmsh session is not re-entrant",
            code="MESH_SESSION",
            hint="finish the running meshing call before starting another one",
        )
    with _LOCK:
        import gmsh

        if gmsh.isInitialized():
            raise MeshError(
                "gmsh is already initialized in this process by other code",
                code="MESH_SESSION",
                hint="run oa_mesh in a worker process that does not use gmsh elsewhere",
            )
        _STATE.active = True
        try:
            gmsh.initialize([], readConfigFiles=False, run=False, interruptible=False)
            gmsh.option.setNumber("General.Terminal", 1 if verbose else 0)
            gmsh.option.setNumber("General.Verbosity", 5 if verbose else 0)
            gmsh.option.setNumber("General.NumThreads", 1)
            # interfaces.md names General.RandomSeed; gmsh >= 4.12 calls the option Mesh.RandomSeed.
            gmsh.option.setNumber("Mesh.RandomSeed", RANDOM_SEED)
            gmsh.option.setNumber("Mesh.MaxNumThreads1D", 1)
            gmsh.option.setNumber("Mesh.MaxNumThreads2D", 1)
            gmsh.option.setNumber("Mesh.MaxNumThreads3D", 1)
            gmsh.option.setNumber("Mesh.Algorithm", 6)
            gmsh.option.setNumber("Mesh.Algorithm3D", 10)
            gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
            gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 1)
            gmsh.option.setNumber("Mesh.RecombineAll", 0)
            gmsh.model.add("oa_mesh")
            yield gmsh
        finally:
            try:
                if gmsh.isInitialized():
                    gmsh.finalize()
            finally:
                _STATE.active = False
