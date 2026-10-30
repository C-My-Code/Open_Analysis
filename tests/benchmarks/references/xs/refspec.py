# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Canonical reference problems of WP-510 (B-EM-01, B-EM-02b, B-IMP-02) and the 2D FEM drivers.

One definition per problem. Every solver (BEM, 2D FEM, Palace, openEMS, oa-fdtd) is configured from
these numbers; ``specs/*.toml`` documents them and ``tests/benchmarks/references/xs/test_*`` pins the
two against each other. Run the generators from the repository root with ``uv run python``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from oa_impedance.fem.electrostatic import prepare_electro, solve_electro
from oa_impedance.fem.solver import FemParams, _lref, _mesh
from oa_model.materials import ConstantDkDf, DjordjevicSarkar
from oa_model.xsection import (
    CrossSection,
    XsConductor,
    XsDielectric,
    XsPlane,
    XsSlab,
    XsTrapezoid,
)

F64 = npt.NDArray[np.float64]
C0 = 299792458.0
SIGMA_CU = 5.8e7  # [S/m] annealed copper, the value of every canonical conductor

# --------------------------------------------------------------------------------- B-EM canonical
EM_W = 0.38e-3  # [m] line width
EM_H = 0.2e-3  # [m] dielectric thickness
EM_T = 35e-6  # [m] copper thickness (1 oz), rectangular cross-section (no etch factor)
EM_L = 25e-3  # [m] line length
EM_DK, EM_DF, EM_FREF = 4.3, 0.02, 1.0e9  # Djordjevic-Sarkar anchor: Dk, tan d at 1 GHz
DS_MODEL = DjordjevicSarkar(dk=EM_DK, df=EM_DF, f_ref=EM_FREF)  # f_low/f_high: library defaults


def em_microstrip_xs(model: Any = DS_MODEL, sigma: float = SIGMA_CU) -> CrossSection:
    """B-EM-01 cross-section: 35 um ground plane, 0.2 mm dielectric, 35 um x 0.38 mm trace."""
    return CrossSection(
        conductors=(
            XsConductor(
                id="g1", role="ground", shape=XsPlane(z_bottom=0.0, thickness=EM_T), conductivity=sigma
            ),
            XsConductor(
                id="s1",
                role="signal",
                shape=XsTrapezoid(
                    u_center=0.0, z_bottom=EM_T + EM_H, w_bottom=EM_W, w_top=EM_W, thickness=EM_T
                ),
                conductivity=sigma,
            ),
        ),
        dielectrics=(XsDielectric(region=XsSlab(z_bottom=EM_T, z_top=EM_T + EM_H), model=model),),
        topology_hint="microstrip",
        symmetric_about_u0=True,
    )


def frozen_model(f: float) -> ConstantDkDf:
    """The DS model evaluated once at ``f`` (what Palace 0.18 does inside a band)."""
    from oa_impedance import _physics_compat as phys

    e = phys.eps_complex(DS_MODEL, f)
    return ConstantDkDf(dk=e.real, df=-e.imag / e.real)


# ------------------------------------------------------------------------------ FEM level driver


@dataclass
class LevelResult:
    level: int
    h_max: float
    n_dofs: int
    c: F64  # (n, n) static Maxwell C at the anchor [F/m]
    l_ext: F64  # (n, n) [H/m]


def fem_levels(xs: CrossSection, n_levels: int = 4, p: FemParams | None = None) -> list[LevelResult]:
    """Electrostatic P2 FEM on ``n_levels`` globally refined meshes (h_max / sqrt 2 per level)."""
    p = p or FemParams(loss="none")
    h = p.h_max if p.h_max is not None else _lref(xs, p) / 4.0
    out: list[LevelResult] = []
    for lev in range(n_levels):
        fm = _mesh(xs, h, p, conductors=False)
        es = solve_electro(prepare_electro(fm, xs), np.array([0.0]))
        out.append(LevelResult(lev, h, int(es.n_dofs), np.array(es.C[0].real), np.array(es.L_ext)))
        h /= math.sqrt(2.0)
        p = FemParams(**{**p.__dict__, "corner_refine": p.corner_refine * 0.8})
    return out


def z0_eeff(c: F64, l_ext: F64) -> tuple[float, float]:
    """(Z0, eps_eff) of a single line from the static C and vacuum-referenced L."""
    c11, l11 = float(c[0, 0]), float(l_ext[0, 0])
    return math.sqrt(l11 / c11), c11 * l11 * C0 * C0


def richardson(vals: list[float]) -> dict[str, float]:
    """Extrapolate a monotone sequence from three successive levels (Aitken); order from the data.

    Returns the extrapolated value, the observed order estimate (per level) and an error estimate
    ``|extrapolated - finest|``. Falls back to the finest value with ``order = nan`` when the last
    three differences do not shrink monotonically.
    """
    a, b, c = vals[-3], vals[-2], vals[-1]
    d1, d2 = b - a, c - b
    if d1 * d2 > 0 and abs(d2) < abs(d1):
        r = d2 / d1
        ext = c + d2 * r / (1.0 - r)
        return {"value": ext, "ratio": r, "err": abs(ext - c), "finest": c}
    return {"value": c, "ratio": float("nan"), "err": abs(d2), "finest": c}
