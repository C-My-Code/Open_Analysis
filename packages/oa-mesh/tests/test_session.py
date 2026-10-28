# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""gmsh lifecycle: lazy import, non-re-entrancy, thread and process safety."""

from __future__ import annotations

import subprocess
import sys
import threading

import pytest
from helpers import piece, poly, rect

from oa_mesh import MeshError, mesh_copper_sheets
from oa_mesh._session import gmsh_session, gmsh_version


def test_types_only_import_never_loads_gmsh() -> None:
    code = (
        "import sys\n"
        "import oa_mesh, oa_mesh.types, oa_mesh.errors\n"
        "from oa_mesh import SheetPiece, Contact, SizeField, XsMesh, CleanOptions, MeshError\n"
        "assert 'gmsh' not in sys.modules, 'gmsh was imported'\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "ok"


def test_meshing_initializes_and_finalizes_gmsh() -> None:
    import gmsh  # type: ignore[import-untyped]

    assert not gmsh.isInitialized()
    (m,) = mesh_copper_sheets([piece([poly(rect(0, 0, 1e-3, 1e-3))])], h_default=0.5e-3)
    assert m.quality.n_triangles > 0
    assert not gmsh.isInitialized()
    assert m.gmsh_version == gmsh_version()


def test_session_is_not_reentrant() -> None:
    with gmsh_session():
        with pytest.raises(MeshError) as ei, gmsh_session():
            pass
        assert ei.value.code == "MESH_SESSION"
        # the outer session is still usable
        import gmsh

        assert gmsh.isInitialized()
    import gmsh

    assert not gmsh.isInitialized()
    with gmsh_session():  # and a new one can start afterwards
        pass


def test_refuses_to_touch_gmsh_initialized_elsewhere() -> None:
    import gmsh

    gmsh.initialize([], readConfigFiles=False, run=False, interruptible=False)
    try:
        with pytest.raises(MeshError) as ei, gmsh_session():
            pass
        assert ei.value.code == "MESH_SESSION"
        assert gmsh.isInitialized()  # untouched
    finally:
        gmsh.finalize()


def test_session_recovers_after_error_inside() -> None:
    with pytest.raises(RuntimeError), gmsh_session():
        raise RuntimeError("boom")
    import gmsh

    assert not gmsh.isInitialized()
    with gmsh_session():
        pass


def test_threads_are_serialized_and_deterministic() -> None:
    pc = piece([poly(rect(0, 0, 6e-3, 4e-3))])
    results: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def work() -> None:
        try:
            (m,) = mesh_copper_sheets([pc], h_default=0.5e-3)
            with lock:
                results.append(m.content_hash())
        except BaseException as exc:
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len(results) == 6 and len(set(results)) == 1


def test_concurrent_processes_are_independent() -> None:
    code = (
        "from oa_mesh import *;from oa_model.arrays import Points;"
        "from oa_model.geometry import Polygon;"
        "p=Polygon(exterior=Points([(0,0),(5e-3,0),(5e-3,3e-3),(0,3e-3)]));"
        "m,=mesh_copper_sheets([SheetPiece(id='a',polygons=(p,))],h_default=0.4e-3);"
        "print(m.content_hash())"
    )
    procs = [
        subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        for _ in range(3)
    ]
    outs = [p.communicate()[0].strip() for p in procs]
    assert all(p.returncode == 0 for p in procs)
    assert len(set(outs)) == 1 and len(outs[0]) == 64
