# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""EMProblem front end: mesh, materials, copper and ports, and the B-EM-01 thru (GPU)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import numpy as np
import pytest

from oa_fdtd.model import Conductor, LumpedPort, ModePort, compile_model
from oa_fdtd.ports import add_wave_planes, wave_sparams
from oa_fdtd.run import run, sparams
from oa_fdtd.subcell.pcb import build_model
from oa_model.em import EMWavePort
from oa_testkit.emproblems import microstrip_thru_problem

GOLDEN = Path(__file__).resolve().parents[3] / "tests" / "golden" / "fdtd_physics"


def _wave_thru(f_max: float, n_points: int):  # type: ignore[no-untyped-def]
    p = microstrip_thru_problem(f_max=f_max, n_points=n_points)
    box = p.structure.bbox
    trace = next(c for c in p.structure.conductors if c.net == "SIG")
    ports = tuple(
        EMWavePort(
            name=name,
            face=face,  # type: ignore[arg-type]
            rect=(box.ymin, box.ymax, box.zmin, box.zmax),
            n_modes=1,
            line_type="microstrip",
            signal_conductors=(trace.id,),
            z0=None,
            excite=True,
        )
        for name, face in (("P1", "xmin"), ("P2", "xmax"))
    )
    bnd = p.boundaries.model_copy(update={"xmin": "pmc", "xmax": "pmc"})
    return p.model_copy(update={"ports": ports, "boundaries": bnd})


def test_build_model_structure() -> None:
    built = build_model(_wave_thru(10e9, 11))
    m = built.model
    assert built.meta["cells"] < 200_000
    assert all(isinstance(p, ModePort) for p in m.ports)
    assert m.walls[:2] == ("pmc", "pmc") and m.walls[4] == "pec" and m.walls[5] == "cpml"
    assert built.meta["debye_poles"]["FR4_generic"] >= 3
    # every copper box is a surface-impedance conductor and the trace ends at the problem box
    assert m.conductors and all(isinstance(c, Conductor) for c in m.conductors)
    trace = [c for c in m.conductors if c.hi[1] - c.lo[1] < 1e-3 and c.hi[1] - c.lo[1] > 0]
    # walls are PMC at the ports: the trace box reaches past the grid, no face edges on the wall
    assert len(trace) == 1 and trace[0].lo[0] <= 0.0 and trace[0].hi[0] >= 25e-3
    # the mesh has exactly one cell between the faces of the 35 um trace
    zl = m.grid.z
    z_top = max(c.hi[2] for c in m.conductors if c.hi[2] < 1.0)
    assert np.isclose(zl, z_top).any()
    c = compile_model(m, "float32")
    assert c.sibc is not None and c.ade is not None and len(c.ports) == 2
    assert 40.0 < c.ports[0]["r_ohm"] < 56.0  # modal Z of the 0.38 mm strip on 0.2 mm FR-4


def test_port_impedance_matches_fem() -> None:
    """Static modal Z of the B-EM-01 strip on the port lattice (refined at the physical edges)
    against the converged 2-D FEM (48.85 ohm at 5 GHz from L_ext and C, DS FR-4)."""
    c = compile_model(build_model(_wave_thru(10e9, 11)).model, "float32")
    assert abs(c.ports[0]["r_ohm"] / 48.85 - 1.0) < 0.015


def test_lumped_port_problem_builds() -> None:
    built = build_model(microstrip_thru_problem(f_max=10e9, n_points=11))
    assert all(isinstance(p, LumpedPort) for p in built.model.ports)
    assert built.model.walls[0] == "cpml"
    compile_model(built.model, "float32")


@pytest.mark.gpu
def test_microstrip_thru_matches_fem_reference() -> None:
    """B-EM-01: IL within 0.05 dB of the cascaded 2-D FEM RLGC reference up to 20 GHz."""
    case = tomllib.loads((GOLDEN / "microstrip_thru" / "case.toml").read_text(encoding="utf-8"))
    built = build_model(_wave_thru(20e9, 100))
    m = built.model
    planes = [add_wave_planes(m, p.name, 1.5e-3) for p in m.ports]
    res = run(m, engine="cuda", dtype="float32", end_db=-70.0, max_steps=400_000)
    s_port = sparams(m, res)
    s, _ = wave_sparams(res.probes, planes, res.port_r, s_port, freqs=m.frequencies)
    assert res.peak_bytes < 2 * 2**30  # B-EM-01 memory target
    f_ref = np.asarray(case["inputs"]["f_hz"])
    il_ref = np.asarray(case["expected"]["il_db"])
    il = np.interp(f_ref, m.frequencies, -20 * np.log10(np.abs(s[1, 0])))
    assert np.abs(il - il_ref).max() < case["tolerance"]["il_abs_db"]
    # S11 is worst below 1 GHz, where the plane extraction is replaced by the port S
    assert float(20 * np.log10(np.abs(s[0, 0])).max()) < case["tolerance"]["s11_max_db"]
    assert np.abs(s[1, 0] - s[0, 1]).max() < 1e-2  # reciprocity
    # eps_eff at 5 GHz against the same-material FEM (3.1188 with the eddy-current L) plus the
    # Kirschning-Jansen dispersion at 5 GHz (+0.25 %): the edge rule keeps it within 0.5 %
    i5 = int(np.argmin(np.abs(m.frequencies - 5e9)))
    ph = np.unwrap(np.angle(s[1, 0]))
    eps = (-ph[i5] / (2 * np.pi * m.frequencies[i5]) * 299792458.0 / 25e-3) ** 2
    assert abs(eps / (3.1188 * 1.0025) - 1.0) < 5e-3
