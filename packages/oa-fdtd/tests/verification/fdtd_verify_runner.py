# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the B-EM cases on oa-fdtd (GPU) or openEMS and score them against their references.

``python -m fdtd_verify_runner --case bem01 --solver fdtd`` (from ``tests/verification``) prints one
JSON line with the accuracy numbers, wall time and GPU memory; ``--out`` also saves the S-matrix.
References (BENCHMARKS.md section 3, labelled in the output):

- ``rlgc``: cascaded 2-D FEM RLGC (``oa_impedance.fem``, same material and copper) of the
  uniform line, renormalised to the port impedance (R1 of B-EM-01);
- ``analytic``: closed form (lossless stripline: ``|S21| = 1``, ``phase = -beta L``);
- ``refine``: the same solver on a finer mesh (``fine`` against ``medium``).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

C0 = 299_792_458.0
ComplexArray = npt.NDArray[np.complex128]
FloatArray = npt.NDArray[np.float64]
REPO = Path(__file__).resolve().parents[4]
_OPENEMS_WT = REPO.parent / "agent-a42ff817b09dd0eb5" / ".oa" / "tools" / "openems"


def db(x: Any) -> Any:
    return 20.0 * np.log10(np.maximum(np.abs(x), 1e-15))


# ------------------------------------------------------------------------------- solvers
def solve_fdtd(problem: Any) -> dict[str, Any]:
    from oa_fdtd.adapter.sim import run_meta, solve_problem

    t0 = time.perf_counter()
    r = solve_problem(problem)
    wall = time.perf_counter() - t0
    return {
        "freqs": r.freqs,
        "s": r.s,
        "z0": r.z0,
        "names": r.port_names,
        "wall_s": wall,
        "meta": run_meta(r),
        "gpu_gib": r.run.peak_bytes / 2**30,
    }


def solve_openems(problem: Any, threads: int = 4) -> dict[str, Any]:
    """The openEMS adapter, one run per excitation, CPU ``threads`` (other worktree's install)."""
    import tempfile

    from oa_em_openems import OpenEMSAdapter
    from oa_framework.external import ExternalResult, ToolInfo

    py = Path(os.environ.get("OA_OPENEMS_PYTHON", _OPENEMS_WT / "venv" / "Scripts" / "python.exe"))
    home = Path(os.environ.get("OA_OPENEMS_HOME", _OPENEMS_WT / "v0.37.0" / "openEMS"))
    expert = dict(problem.mesh.expert)
    expert.setdefault("openems.absorbing", "MUR")
    problem = problem.model_copy(update={"mesh": problem.mesh.model_copy(update={"expert": expert})})
    adapter = OpenEMSAdapter()
    wd = Path(tempfile.mkdtemp(prefix="oa_openems_"))
    prep = adapter.prepare(problem, wd, ToolInfo(program="openems-python", runner="local", path=str(py)))
    results = []
    wall = 0.0
    for cmd in prep.commands:
        env = {**os.environ, **cmd.env, "OA_NUM_THREADS": str(threads), "CSXCAD_INSTALL_PATH": str(home)}
        env["PATH"] = str(home) + os.pathsep + env.get("PATH", "")
        t0 = time.perf_counter()
        p = subprocess.run(
            [str(py), *cmd.args], cwd=cmd.workdir, env=env, capture_output=True, text=True, check=False
        )
        w = time.perf_counter() - t0
        wall += w
        results.append(
            ExternalResult(
                returncode=p.returncode,
                stdout_tail="\n".join(p.stdout.splitlines()[-200:]),
                stderr_tail="\n".join(p.stderr.splitlines()[-200:]),
                wall_s=w,
                runner="local",
            )
        )
    out = adapter.collect(problem, prep, results)
    if out.network is None:
        raise RuntimeError("; ".join(f"{d.code}: {d.message}" for d in out.warnings))
    net = out.network
    return {
        "freqs": np.asarray(net.freqs.array),
        "s": np.asarray(net.s.array),
        "z0": np.real(np.asarray(net.z0.array)),
        "names": net.port_names,
        "wall_s": wall,
        "meta": {"engine": "openems", "threads": threads, "runs": len(results), "workdir": str(wd)},
        "gpu_gib": 0.0,
    }


# ------------------------------------------------------------------------------ references
def rlgc_reference(kind: str, freqs: FloatArray) -> dict[str, FloatArray]:
    """FEM RLGC(f) of the testkit line (``microstrip`` or ``stripline``) at ``freqs`` [Hz]."""
    from oa_geometry import BoardGeometry
    from oa_impedance.fem.solver import solve_fem
    from oa_testkit import builders as bld

    board = bld.microstrip_board() if kind == "microstrip" else bld.stripline_board()
    geo = BoardGeometry.build(board)
    seg = geo.segments("SIG")[0]
    xs = geo.cross_section_at(seg, 12e-3, include_aggressors=False)
    res = solve_fem(xs, freqs)
    r = res.rlgc
    n = len(freqs)
    return {
        "freqs": np.asarray(freqs, dtype=np.float64),
        "R": np.asarray(r.R.array).reshape(n),
        "L": np.asarray(r.L.array).reshape(n),
        "G": np.asarray(r.G.array).reshape(n),
        "C": np.asarray(r.C.array).reshape(n),
    }


def cascade_s(
    freqs: FloatArray, rlgc: dict[str, FloatArray], length: float, z_ref: float
) -> tuple[ComplexArray, ComplexArray]:
    """``(S11, S21)`` of a uniform line from RLGC (interpolated in log f) between ``z_ref``."""
    lf = np.log(rlgc["freqs"])
    q = {k: np.interp(np.log(freqs), lf, rlgc[k]) for k in "RLGC"}
    w = 2 * np.pi * freqs
    z = q["R"] + 1j * w * q["L"]
    y = q["G"] + 1j * w * q["C"]
    gam, zc = np.sqrt(z * y), np.sqrt(z / y)
    a = np.cosh(gam * length)
    b = zc * np.sinh(gam * length)
    c = np.sinh(gam * length) / zc
    den = a * z_ref + b + c * z_ref * z_ref + a * z_ref
    return (a * z_ref + b - c * z_ref * z_ref - a * z_ref) / den, 2 * z_ref / den


def thru_scores(
    f: FloatArray, s: ComplexArray, z0: FloatArray, ref: tuple[ComplexArray, ComplexArray]
) -> dict[str, float]:
    """B-EM-01/02b: IL error, S11 rule and delay error against a (S11, S21) reference."""
    s11r, s21r = ref
    il = -db(s[:, 1, 0])
    il_ref = -db(s21r)
    d_il = np.abs(il - il_ref)
    s11 = db(s[:, 0, 0])
    s11_ref = db(s11r)
    above = s11_ref > -30.0
    ph = np.unwrap(np.angle(s[:, 1, 0]))
    ph_ref = np.unwrap(np.angle(s21r))
    k = len(f) // 2
    delay = float(-ph[-1] / (2 * np.pi * f[-1]))
    delay_ref = float(-ph_ref[-1] / (2 * np.pi * f[-1]))
    return {
        "il_err_max_db": float(d_il.max()),
        "il_err_20ghz_db": float(d_il[-1]),
        "il_err_mid_db": float(d_il[k]),
        "s11_max_db": float(s11.max()),
        "s11_err_above_m30_db": float(np.max(np.abs(s11 - s11_ref)[above])) if above.any() else 0.0,
        "s11_ref_max_db": float(s11_ref.max()),
        "delay_err_pct": float(100.0 * abs(delay - delay_ref) / delay_ref),
        "z0_ohm": float(z0[0]),
    }


def stripline_exact_scores(f: FloatArray, s: ComplexArray, er: float, length: float) -> dict[str, float]:
    """B-EM-02a: |S21| within 0.01 dB of 0, phase against ``-beta L``, S11."""
    beta = 2 * np.pi * f * np.sqrt(er) / C0
    ph_ref = np.unwrap(-beta * length)
    ph = np.unwrap(np.angle(s[:, 1, 0]))
    ph0 = ph - 2 * np.pi * np.round((ph - ph_ref)[0] / (2 * np.pi))
    err = np.degrees(np.abs(ph0 - ph_ref))
    band = f >= 1e9
    return {
        "s21_err_max_db": float(np.abs(db(s[:, 1, 0])).max()),
        "s21_err_ge1ghz_db": float(np.abs(db(s[band, 1, 0])).max()),
        "phase_err_max_deg": float(err.max()),
        "phase_err_20ghz_deg": float(err[-1]),
        "s11_max_db": float(db(s[:, 0, 0]).max()),
        "s11_max_ge1ghz_db": float(db(s[band, 0, 0]).max()),
    }


def mixed_mode(s: ComplexArray) -> dict[str, ComplexArray]:
    """Modal S of the two-mode ports ``P1:m0 P1:m1 P2:m0 P2:m1`` (even, odd): SDD/SCC/SCD."""
    return {
        "sdd21": s[:, 3, 1],
        "sdd11": s[:, 1, 1],
        "sdd22": s[:, 3, 3],
        "scc21": s[:, 2, 0],
        "scd21": s[:, 2, 1],
        "sdc21": s[:, 3, 0],
    }


# ---------------------------------------------------------------------------------- cases
def build_case(case: str, quality: str, cpw: float | None = None) -> Any:
    import fdtd_verify_cases as cases

    if case == "bem01":
        return cases.microstrip_thru(quality=quality)
    if case == "bem02a":
        return cases.stripline_exact(quality=quality, cpw=cpw)
    if case == "bem02b":
        return cases.stripline_lossy(quality=quality)
    if case == "bem03":
        return cases.diff_via(quality=quality, cpw=cpw)
    if case == "bem03s":
        return cases.diff_via(quality=quality, straight=True, cpw=cpw)
    if case == "bem03c":
        return cases.diff_via(quality=quality, half="even", cpw=cpw)
    if case == "bem03stub":
        return cases.diff_via(quality=quality, stub=0.6e-3, cpw=cpw)
    if case == "bem04":
        return cases.cpwg_launch(quality=quality)
    if case == "bem05a":
        return cases.patch_antenna(quality=quality)
    if case == "bem06b":
        return cases.msl_notch(quality=quality)
    raise SystemExit(f"unknown case {case}")


def score(case: str, res: dict[str, Any], problem: Any, rlgc: dict[str, FloatArray] | None) -> dict[str, Any]:
    f, s, z0 = res["freqs"], res["s"], res["z0"]
    out: dict[str, Any] = {}
    if case in ("bem01", "bem02b"):
        kind = "microstrip" if case == "bem01" else "stripline"
        if rlgc is None:
            rlgc = rlgc_reference(kind, np.geomspace(1e8, 2e10, 48))
        ref = cascade_s(f, rlgc, 25e-3, float(z0[0]))
        out = thru_scores(f, s, z0, ref)
        out["reference"] = "rlgc (FEM, converged)"
    elif case == "bem02a":
        out = stripline_exact_scores(f, s, 4.0, 25e-3)
        from fdtd_verify_cases import cohn_z0

        out["z0_ohm"] = float(z0[0])
        out["z0_cohn_ohm"] = cohn_z0(4.0, 0.2e-3, 0.4e-3)
        out["reference"] = "analytic (Cohn Z0, TEM phase)"
    elif case.startswith("bem03"):
        i20 = f <= 20e9
        out = {
            "sdd21_il_20ghz_db": float(-db(s[:, 1, 0])[i20][-1]),
            "sdd21_il_40ghz_db": float(-db(s[-1, 1, 0])),
            "sdd11_max_db": float(db(s[:, 0, 0]).max()),
            "sdd11_max_20ghz_db": float(db(s[:, 0, 0])[i20].max()),
            "sdd21_phase_20ghz_deg": float(np.degrees(np.unwrap(np.angle(s[:, 1, 0]))[i20][-1])),
            "z_half_ohm": float(z0[0]),
            "reference": "refine (see compare)",
        }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", required=True)
    ap.add_argument("--solver", default="fdtd", choices=["fdtd", "openems"])
    ap.add_argument("--quality", default="medium")
    ap.add_argument("--cpw", type=float, default=None)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--rlgc", type=Path, default=None)
    a = ap.parse_args(argv)
    problem = build_case(a.case, a.quality, a.cpw)
    res = solve_fdtd(problem) if a.solver == "fdtd" else solve_openems(problem, a.threads)
    rl = dict(np.load(a.rlgc)) if a.rlgc else None
    sc = score(a.case, res, problem, rl)
    line = {
        "case": a.case,
        "solver": a.solver,
        "quality": a.quality,
        "wall_s": round(res["wall_s"], 2),
        "gpu_gib": round(res["gpu_gib"], 3),
        "meta": {k: v for k, v in res["meta"].items() if k in ("cells", "steps", "stopped_by", "energy_db", "engine", "tail", "threads")},
        **sc,
    }
    print(json.dumps(line, default=float))
    if a.out is not None:
        np.savez(a.out, freqs=res["freqs"], s=res["s"], z0=res["z0"], names=np.array(res["names"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
