# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""BEM (oa_impedance default mesh) against R1 on the canonical microstrip, 0.1-20 GHz.

``uv run python tests/benchmarks/references/xs/compare_bem_r1.py`` prints a table and, with
``--write``, stores it in ``R1/bem_vs_r1.json``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import gen_r1
import refspec as rs

from oa_impedance.service import ImpedanceServiceImpl

HERE = Path(__file__).parent


def main() -> None:
    ref = json.loads((HERE / "R1" / "reference.json").read_text(encoding="utf-8"))
    f = np.array(ref["freqs_hz"])
    res = ImpedanceServiceImpl().solve(rs.em_microstrip_xs(), f, method="bem")
    rl = res.rlgc
    assert rl is not None
    one = lambda a: np.asarray(a.array)[:, 0, 0]  # noqa: E731
    casc = gen_r1.cascade(f, one(rl.R), one(rl.L), one(rl.G), one(rl.C), rs.EM_L)
    rows = []
    for key in ("il_line_db", "z0", "eps_eff"):
        r = np.array(ref["reference"][key])
        d = np.array(casc[key]) - r
        rows.append(
            {
                "quantity": key,
                "max_abs_diff": float(np.abs(d).max()),
                "diff_at_10ghz": float(d[list(f).index(10e9)]),
                "max_rel_pct": float(100 * np.abs(d / r).max()),
            }
        )
    ac = np.array(casc["alpha_c_np_m"]) * rs.EM_L * 8.685889638
    ad = np.array(casc["alpha_d_np_m"]) * rs.EM_L * 8.685889638
    rac = np.array(ref["reference"]["alpha_c_np_m"]) * rs.EM_L * 8.685889638
    rad = np.array(ref["reference"]["alpha_d_np_m"]) * rs.EM_L * 8.685889638
    i10 = list(f).index(10e9)
    out = {
        "rows": rows,
        "alpha_c_db_10ghz": {"bem": float(ac[i10]), "r1": float(rac[i10])},
        "alpha_d_db_10ghz": {"bem": float(ad[i10]), "r1": float(rad[i10])},
        "alpha_c_rel_err_pct_max": float(100 * np.abs(ac / rac - 1).max()),
        "alpha_d_rel_err_pct_max": float(100 * np.abs(ad / rad - 1).max()),
    }
    print(json.dumps(out, indent=1))
    if "--write" in sys.argv:
        (HERE / "R1" / "bem_vs_r1.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
