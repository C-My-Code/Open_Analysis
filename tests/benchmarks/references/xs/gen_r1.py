# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""R1 of B-EM-01: converged 2D RLGC(f) of the canonical microstrip, cascaded over 25 mm.

``uv run python tests/benchmarks/references/xs/gen_r1.py`` writes ``R1/reference.json``. The
reference quantities are Richardson-extrapolated over 4 uniformly refined meshes of the P2 FEM
(electrostatic C, vacuum L, and the impedance-boundary eddy solve on each mesh). Variants hold the
single physical difference that other solvers have (dielectric frozen per band, ...), so a
residual against Palace or openEMS can be attributed.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

sys.path.insert(0, str(Path(__file__).parent))
import refspec as rs

from oa_impedance.fem import FemParams, solve_fem

F64 = npt.NDArray[np.float64]
OUT = Path(__file__).parent / "R1"
FREQS = np.array([0.1, 0.25, 0.5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 16, 18, 20]) * 1e9


def cascade(
    f: F64, r: F64, ll: F64, g: F64, c: F64, length: float, z_ref: float = 50.0
) -> dict[str, F64]:
    """Uniform line (per-metre R, L, G, C at each f) as a 2-port in ``z_ref`` and in its own Zc."""
    w = 2 * np.pi * f
    z, y = r + 1j * w * ll, g + 1j * w * c
    zc = np.sqrt(z / y)
    gam = np.sqrt(z * y)
    gam = np.where(gam.real < 0, -gam, gam)
    t = np.exp(-gam * length)
    # S of a line in its own impedance: S21 = e^{-gl}, S11 = 0; in z_ref via the reflection at the ends
    rho = (zc - z_ref) / (zc + z_ref)
    s11 = rho * (1 - t * t) / (1 - rho * rho * t * t)
    s21 = (1 - rho * rho) * t / (1 - rho * rho * t * t)
    alpha_c = r / (2 * zc.real)
    alpha_d = g * zc.real / 2
    return {
        "z0": zc.real,
        "alpha_np_m": gam.real,
        "alpha_c_np_m": alpha_c,
        "alpha_d_np_m": alpha_d,
        "il_line_db": 8.685889638065035 * gam.real * length,
        "il_50_db": -20 * np.log10(np.abs(s21)),
        "s11_50_db": 20 * np.log10(np.abs(s11)),
        "delay_s": gam.imag / w * length,
        "eps_eff": (gam.imag * rs.C0 / w) ** 2,
        "s21_50_re": s21.real,
        "s21_50_im": s21.imag,
    }


def run(xs: Any, levels: int, freqs: F64 = FREQS) -> dict[str, F64]:
    res = solve_fem(xs, freqs, FemParams(rtol=0.0, max_levels=levels))
    rl = res.rlgc
    assert rl is not None
    one = lambda a: np.asarray(a.array)[:, 0, 0]  # noqa: E731
    return cascade(freqs, one(rl.R), one(rl.L), one(rl.G), one(rl.C), rs.EM_L)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    t0 = time.time()
    xs = rs.em_microstrip_xs()
    per_level: list[dict[str, F64]] = []
    table: list[dict[str, Any]] = []
    i10 = int(np.argmin(abs(FREQS - 10e9)))
    for lev in range(4):  # lev = refinement levels after the first mesh; solved from scratch each
        t = time.time()
        out = run(xs, lev)
        per_level.append(out)
        table.append(
            {
                "meshes": lev + 1,
                "il_line_10ghz_db": float(out["il_line_db"][i10]),
                "z0_10ghz_ohm": float(out["z0"][i10]),
                "eps_eff_10ghz": float(out["eps_eff"][i10]),
                "alpha_c_10ghz_db": float(8.6859 * out["alpha_c_np_m"][i10] * rs.EM_L),
                "alpha_d_10ghz_db": float(8.6859 * out["alpha_d_np_m"][i10] * rs.EM_L),
                "wall_s": round(time.time() - t, 1),
            }
        )
        print(table[-1], flush=True)
    ref: dict[str, Any] = {}
    err: dict[str, Any] = {}
    for key in ("z0", "eps_eff", "il_line_db", "il_50_db", "alpha_c_np_m", "alpha_d_np_m", "delay_s"):
        stack = np.stack([lv[key] for lv in per_level])  # (4, nf)
        ext = np.empty(stack.shape[1])
        e = np.empty(stack.shape[1])
        for k in range(stack.shape[1]):
            r_ = rs.richardson(list(stack[:, k]))
            ext[k], e[k] = r_["value"], r_["err"]
        ref[key], err[key] = ext.tolist(), e.tolist()
    # S11 and the complex S21 follow from the extrapolated RLGC-equivalents only approximately;
    # report the finest mesh for them (|S11| < -35 dB, not gated against R1 tighter than 1 dB)
    fine = per_level[-1]
    variants: dict[str, Any] = {}
    for name, model in (("ds_frozen_5ghz", rs.frozen_model(5e9)),):
        v = run(rs.em_microstrip_xs(model=model), 3)
        variants[name] = {
            "il_line_db": v["il_line_db"].tolist(),
            "z0": v["z0"].tolist(),
            "eps_eff": v["eps_eff"].tolist(),
        }
    doc = {
        "schema": "oa-bench-reference/1",
        "id": "R1",
        "case": "B-EM-01",
        "spec": "tests/benchmarks/references/xs/specs/canonical-microstrip.toml",
        "method": "2D P2 FEM, electrostatic C + vacuum L, eddy-current impedance-boundary R(f); "
        "Djordjevic-Sarkar eps(f) per frequency; Richardson over 4 meshes",
        "length_m": rs.EM_L,
        "freqs_hz": FREQS.tolist(),
        "reference": ref,
        "error_estimate": err,
        "finest_mesh": {k: v.tolist() for k, v in fine.items()},
        "convergence": table,
        "variants": variants,
        "provenance": {"wall_s": round(time.time() - t0, 1), "fem": "oa_impedance.fem"},
    }
    (OUT / "reference.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print("done", doc["provenance"])


if __name__ == "__main__":
    main()
