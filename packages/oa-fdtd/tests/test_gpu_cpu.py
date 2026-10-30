# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""GPU vs NumPy agreement on a graded, dielectric, PEC-loaded, CPML+PMC problem (ADR-0009 sec 5)."""

from __future__ import annotations

import json

import numpy as np
import pytest
from fdtd_helpers import graded_lines
from oa_fdtd.cli import main
from oa_fdtd.grid import Grid
from oa_fdtd.model import (
    LumpedPort,
    Model,
    Probe,
    Region,
    SoftSource,
    Waveform,
    compile_model,
)
from oa_fdtd.run import run


def _problem(batch: int = 1) -> Model:
    lines = (
        graded_lines(0.024, 24, 1.03),
        graded_lines(0.02, 20, 1.0),
        graded_lines(0.022, 22, 0.97),
    )
    m = Model(
        Grid(*lines),
        Waveform("gauss_mod", 10e9, 20e9),
        np.array([2e9, 6e9, 10e9, 14e9]),
        walls=("cpml", "cpml", "pmc", "pmc", "pec", "pec"),
    )
    m.cpml = type(m.cpml)(cells=4)
    m.regions.append(Region((0, 0, 0.004), (0.024, 0.02, 0.012), eps_r=4.4, sigma=0.02))
    m.regions.append(Region((0.008, 0.006, 0.012), (0.016, 0.014, 0.0121), pec=True))
    m.sources.append(SoftSource("s", 2, (0.012, 0.01, 0.006), (0.012, 0.01, 0.006)))
    m.ports.append(LumpedPort("p", 2, (0.006, 0.01, 0.0), (0.006, 0.01, 0.004), 50.0))
    m.probes.append(Probe("e", "E", 2, (0.016, 0.01, 0.002), (0.016, 0.01, 0.002)))
    m.probes.append(Probe("h", "H", 1, (0.016, 0.01, 0.002), (0.016, 0.01, 0.002)))
    if batch > 1:
        m.drive = np.array([[1.0, 1.0], [0.0, -2.0]])[:batch]
    return m


def test_material_table_and_pec_edges() -> None:
    c = compile_model(_problem(), "float32")
    assert c.eps_tab[0] == 0.0 and c.cb_tab[0] == 0.0  # index 0 is PEC
    assert np.isclose(c.eps_tab, 4.4).any() and np.isclose(c.eps_tab, 1.0).any()
    assert 1 < c.eps_tab.size < 500
    # averaged materials appear at the slab interface
    assert ((c.eps_tab > 1.0) & (c.eps_tab < 4.4)).any()
    _nx, ny, nz = c.grid.n
    assert (c.idx[1][1 : nz + 2, 1 : ny + 1, 1] == 0).all()  # tangential E on the cpml/pec wall


@pytest.mark.parametrize("batch", [1, 2])
def test_numpy_float32_matches_float64(batch: int) -> None:
    m = _problem(batch)
    a = run(m, engine="numpy", dtype="float64", end_db=None, max_steps=400)
    b = run(m, engine="numpy", dtype="float32", end_db=None, max_steps=400)
    for k in a.probes:
        assert np.abs(a.probes[k] - b.probes[k]).max() <= 1e-4 * np.abs(a.probes[k]).max()


@pytest.mark.gpu
@pytest.mark.parametrize("batch", [1, 2])
@pytest.mark.parametrize("dtype", ["float32", "float64"])
def test_cuda_matches_numpy(batch: int, dtype: str) -> None:
    m = _problem(batch)
    ref = run(m, engine="numpy", dtype="float64", end_db=None, max_steps=400)
    gpu = run(m, engine="cuda", dtype=dtype, end_db=None, max_steps=400)
    tol = 1e-4 if dtype == "float32" else 1e-10
    assert gpu.engine == "cuda" and gpu.peak_bytes > 0
    assert gpu.report["backend"] == "cuda"
    for k in ref.probes:
        scale = np.abs(ref.probes[k]).max()
        assert np.abs(ref.probes[k] - gpu.probes[k]).max() <= tol * scale, k


@pytest.mark.gpu
def test_cuda_graph_equals_eager() -> None:
    from oa_fdtd.engine import CupyEngine

    c = compile_model(_problem(), "float32")
    a = CupyEngine(c, "float32", use_graph=True, max_steps=700)
    b = CupyEngine(c, "float32", use_graph=False, max_steps=700)
    a.step(256)
    b.step(256)
    for k, v in a.results().items():
        assert np.array_equal(v, b.results()[k])  # same device, same input: bitwise identical


@pytest.mark.gpu
def test_cuda_is_deterministic() -> None:
    m = _problem()
    a = run(m, engine="cuda", dtype="float32", end_db=None, max_steps=300)
    b = run(m, engine="cuda", dtype="float32", end_db=None, max_steps=300)
    for k in a.probes:
        assert np.array_equal(a.probes[k], b.probes[k])


def test_memory_estimate_and_budget_refusal() -> None:
    from oa_fdtd.engine import estimate_bytes

    c = compile_model(_problem(), "float32")
    cells = int(np.prod(c.grid.shape))
    est = estimate_bytes(c, 4)
    assert cells * 30 < est < cells * 40  # 24 B fields + 6 B index (+ psi) per cell


def test_cli_job_roundtrip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    n = 6
    job = {
        "grid": {k: np.linspace(0, 0.012, n + 1).tolist() for k in "xyz"},
        "waveform": {"kind": "gauss_mod", "f0": 10e9, "bandwidth": 20e9},
        "frequencies": [5e9, 10e9],
        "walls": ["pec"] * 6,
        "sources": [
            {"name": "s", "axis": 2, "lo": [0.006, 0.006, 0.006], "hi": [0.006, 0.006, 0.006]}
        ],
        "probes": [
            {
                "name": "p",
                "kind": "E",
                "axis": 2,
                "lo": [0.004, 0.004, 0.004],
                "hi": [0.004, 0.004, 0.004],
            }
        ],
        "run": {"engine": "numpy", "dtype": "float64", "max_steps": 200, "end_db": None},
        "output": "ports.npz",
    }
    p = tmp_path / "job.json"
    p.write_text(json.dumps(job))
    assert main(["run", str(p)]) == 0
    data = np.load(tmp_path / "ports.npz")
    assert data["probe_p"].shape == (1, 2) and np.abs(data["probe_p"]).max() > 0
    p.write_text(json.dumps({"grid": {}}))
    assert main(["run", str(p)]) == 2
