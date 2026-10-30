# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""B-EM-01 style microstrip thru on oa-fdtd against a cascaded 2-D FEM RLGC reference.

``python bench/thru.py --rlgc rlgc.npz`` where the npz holds ``freqs, R, L, G, C`` (per metre, from
``oa_impedance.fem.solver.solve_fem``; see ``--make-rlgc``). Prints IL and |S11| errors at 10 and
20 GHz, the worst over the band, runtime and the cell count. The reference is R1 of BENCHMARKS.md
(2-D RLGC cascaded over the length and renormalised to the port impedance).
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

from oa_fdtd.ports import add_wave_planes, wave_sparams
from oa_fdtd.run import run, sparams
from oa_fdtd.subcell.pcb import build_model
from oa_model.em import EMProblem, EMWavePort
from oa_testkit.emproblems import microstrip_thru_problem


def thru_problem(
    f_max: float,
    n_points: int,
    quality: str,
    length: float = 25e-3,
    y_half: float = 2.5e-3,
    air: float = 1.0e-3,
) -> EMProblem:
    """The testkit microstrip thru with wave ports on the x faces instead of lumped ports."""
    p = microstrip_thru_problem(
        length=length, f_max=f_max, n_points=n_points, y_half=y_half, air=air
    )
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
    mesh = p.mesh.model_copy(update={"preset": quality})
    return p.model_copy(update={"ports": ports, "boundaries": bnd, "mesh": mesh})


def cascade_s(
    freqs: np.ndarray, rlgc: dict[str, np.ndarray], length: float, z_ref: float
) -> tuple[np.ndarray, np.ndarray]:
    """``(S11, S21)`` of a uniform line from interpolated RLGC between ``z_ref`` terminations."""
    lf = np.log(rlgc["freqs"])
    out = {}
    for k in "RLGC":
        out[k] = np.interp(np.log(freqs), lf, np.asarray(rlgc[k]).reshape(-1))
    w = 2 * np.pi * freqs
    z = out["R"] + 1j * w * out["L"]
    y = out["G"] + 1j * w * out["C"]
    gam = np.sqrt(z * y)
    zc = np.sqrt(z / y)
    a = np.cosh(gam * length)
    b = zc * np.sinh(gam * length)
    c = np.sinh(gam * length) / zc
    den = a * z_ref + b + c * z_ref * z_ref + a * z_ref
    return (a * z_ref + b - c * z_ref * z_ref - a * z_ref) / den, 2 * z_ref / den


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rlgc", type=Path, required=True)
    ap.add_argument("--f-max", type=float, default=20e9)
    ap.add_argument("--points", type=int, default=100)
    ap.add_argument("--quality", default="medium")
    ap.add_argument("--engine", default="cuda")
    ap.add_argument("--dtype", default="float32")
    ap.add_argument("--end-db", type=float, default=-70.0)
    ap.add_argument("--copper", default="thick_sibc")
    ap.add_argument("--y-half", type=float, default=2.5e-3)
    ap.add_argument("--plane-offset", type=float, default=1.5e-3)
    ap.add_argument("--planes", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--air", type=float, default=1.0e-3)
    a = ap.parse_args()
    rl = dict(np.load(a.rlgc))
    t0 = time.perf_counter()
    prob = thru_problem(a.f_max, a.points, a.quality, y_half=a.y_half, air=a.air)
    built = build_model(prob, copper=a.copper)
    planes = [add_wave_planes(built.model, p.name, a.plane_offset) for p in built.model.ports]
    t1 = time.perf_counter()
    res = run(built.model, engine=a.engine, dtype=a.dtype, end_db=a.end_db, max_steps=400_000)
    t2 = time.perf_counter()
    s_port = sparams(built.model, res)
    s, _ = (
        wave_sparams(res.probes, planes, res.port_r, freqs=built.model.frequencies)
        if a.planes
        else (s_port, None)
    )
    f = built.model.frequencies
    s11r, s21r = cascade_s(f, rl, 25e-3, float(res.port_r[0]))
    il = -20 * np.log10(np.abs(s[1, 0]))
    il_ref = -20 * np.log10(np.abs(s21r))
    d_il = np.abs(il - il_ref)
    s11 = 20 * np.log10(np.abs(s[0, 0]))
    s11_ref = 20 * np.log10(np.abs(s11r))
    bad = np.flatnonzero(d_il > 0.05)
    print("IL err > 0.05 dB at GHz:", np.round(f[bad] / 1e9, 2))
    for fq in (10e9, 20e9):
        i = int(np.argmin(np.abs(f - fq)))
        print(
            f"{f[i] / 1e9:5.1f} GHz  IL {il[i]:.4f} dB (ref {il_ref[i]:.4f}, err {d_il[i]:.4f})"
            f"  S11 {s11[i]:.1f} dB (ref {s11_ref[i]:.1f})"
        )
    above = s11_ref > -30.0
    s11_err = float(np.max(np.abs(s11 - s11_ref)[above])) if above.any() else 0.0
    print(
        f"worst IL err {d_il.max():.4f} dB; worst S11 err (ref > -30 dB) {s11_err:.2f} dB; "
        f"max S11 {s11.max():.1f} dB (ref max {s11_ref.max():.1f})"
    )
    print(
        f"cells {built.meta['cells']} shape {built.meta['shape']} steps {res.steps} dt {res.dt:.3e}"
        f" stopped {res.stopped_by} {res.energy_db:.1f} dB; Z_port {res.port_r[0]:.2f} ohm"
    )
    print(f"build {t1 - t0:.2f} s, run {t2 - t1:.2f} s, VRAM peak {res.peak_bytes / 2**30:.2f} GiB")
    print(built.diagnostics, built.meta["debye_poles"], built.meta["debye_err"])


if __name__ == "__main__":
    main()
