# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""B-IMP-02 reference set: 24 PCB cross-sections, converged P2-FEM references with Richardson.

``uv run python tests/benchmarks/references/xs/gen_set24.py [--only NAME] [--levels 4]``
writes ``B-IMP-02/<name>/reference.json`` (existing files are kept unless ``--force``).

12 geometries x {35 um, 18 um} copper. Every trace is a trapezoid with the etch factor of
``spec.etch`` (top narrower than the bottom by one copper thickness), lossless dielectrics with the
static permittivity of the table, copper as PEC for the static quantities. W/h 0.3 .. 3 and
eps_r 3.0 .. 4.5 span the Polar SI9000 library classes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import refspec as rs

from oa_impedance.bem import MeshParams
from oa_impedance.modal import modal_decomposition
from oa_impedance.service import ImpedanceServiceImpl
from oa_model.materials import ConstantDkDf
from oa_model.xsection import (
    CrossSection,
    XsConductor,
    XsDielectric,
    XsPlane,
    XsSlab,
    XsTrapezoid,
)

OUT = Path(__file__).parent / "B-IMP-02"
UM = 1e-6


@dataclass(frozen=True)
class Spec:
    name: str
    kind: str
    h: float  # [um] primary dielectric height (stripline: lower, trace-centre to nothing)
    w: float  # [um] bottom trace width
    er: float  # [1] substrate permittivity
    s: float = 0.0  # [um] edge gap (coupled), coplanar gap (cpwg), vertical gap (broadside)
    h2: float = 0.0  # [um] stripline: dielectric above the trace; coated/embedded: cover height
    er2: float = 0.0  # [1] coating permittivity (coated), 0 = same as er
    pair: bool = False


GEOMS: tuple[Spec, ...] = (
    Spec("ms_w150_h100", "microstrip", 100, 150, 4.2),
    Spec("ms_narrow_w60_h200", "microstrip", 200, 60, 3.0),
    Spec("ms_wide_w300_h100", "microstrip", 100, 300, 4.5),
    Spec("ms_coated", "coated", 150, 200, 4.0, h2=25, er2=3.4),
    Spec("ms_embedded", "embedded", 120, 180, 4.3, h2=120),
    Spec("sl_sym", "stripline", 200, 120, 4.0, h2=200),
    Spec("sl_offset", "stripline", 150, 150, 3.8, h2=300),
    Spec("cpwg", "cpwg", 150, 200, 4.0, s=150),
    Spec("ms_pair", "microstrip", 100, 120, 4.3, s=120, pair=True),
    Spec("sl_pair", "stripline", 200, 100, 4.1, s=100, h2=200, pair=True),
    Spec("bcsl", "broadside", 300, 250, 4.0, s=200, h2=300, pair=True),
    Spec("ms_coated_pair", "coated", 100, 120, 4.3, s=150, h2=25, er2=3.3, pair=True),
)


def _trace(id_: str, u: float, z0: float, w: float, t: float) -> XsConductor:
    return XsConductor(
        id=id_,
        role="signal",
        shape=XsTrapezoid(
            u_center=u * UM, z_bottom=z0, w_bottom=w * UM, w_top=max(w * UM - t, 0.2 * w * UM), thickness=t
        ),
    )


def _slab(z0: float, z1: float, er: float) -> XsDielectric:
    return XsDielectric(region=XsSlab(z_bottom=z0, z_top=z1), model=ConstantDkDf(dk=er, df=0.0))


def build(sp: Spec, t_um: float) -> CrossSection:
    t = t_um * UM
    h, h2 = sp.h * UM, sp.h2 * UM
    pitch = (sp.w + sp.s) / 2.0
    gnd = XsConductor(id="g1", role="ground", shape=XsPlane(z_bottom=0.0, thickness=t))
    z0 = t  # top of the lower plane
    diel: list[XsDielectric] = []
    cond: list[XsConductor] = [gnd]
    if sp.kind in ("microstrip", "coated", "embedded", "cpwg"):
        zt = z0 + h
        us = [-pitch, pitch] if sp.pair else [0.0]
        for k, u in enumerate(us):
            cond.append(_trace(f"s{k + 1}", u, zt, sp.w, t))
        diel.append(_slab(z0, zt, sp.er))
        if sp.kind == "coated":
            diel.append(_slab(zt, zt + t + h2, sp.er2))
        if sp.kind == "embedded":
            diel[0] = _slab(z0, zt + t + h2, sp.er)
        if sp.kind == "cpwg":
            half = (sp.w + 2 * sp.s) / 2.0 * UM
            cond.append(
                XsConductor(id="g2", role="ground", shape=XsPlane(z_bottom=zt, thickness=t, u_max=-half))
            )
            cond.append(
                XsConductor(id="g3", role="ground", shape=XsPlane(z_bottom=zt, thickness=t, u_min=half))
            )
        hint = {"microstrip": "microstrip", "coated": "general", "embedded": "embedded_microstrip", "cpwg": "cpw"}[sp.kind]
    elif sp.kind == "stripline":
        zt = z0 + h  # trace bottom
        us = [-pitch, pitch] if sp.pair else [0.0]
        for k, u in enumerate(us):
            cond.append(_trace(f"s{k + 1}", u, zt, sp.w, t))
        z_top_plane = zt + t + h2
        diel.append(_slab(z0, z_top_plane, sp.er))
        cond.append(XsConductor(id="g2", role="ground", shape=XsPlane(z_bottom=z_top_plane, thickness=t)))
        hint = "general"
    elif sp.kind == "broadside":
        z1 = z0 + h  # lower trace bottom
        z2 = z1 + t + sp.s * UM  # upper trace bottom
        cond.append(_trace("s1", 0.0, z1, sp.w, t))
        cond.append(_trace("s2", 0.0, z2, sp.w, t))
        z_top_plane = z2 + t + h2
        diel.append(_slab(z0, z_top_plane, sp.er))
        cond.append(XsConductor(id="g2", role="ground", shape=XsPlane(z_bottom=z_top_plane, thickness=t)))
        hint = "general"
    else:
        raise ValueError(sp.kind)
    return CrossSection(
        conductors=tuple(cond),
        dielectrics=tuple(diel),
        topology_hint=hint,  # type: ignore[arg-type]
        symmetric_about_u0=True,
    )


def quantities(c: Any, l_ext: Any, pair: bool) -> dict[str, float]:
    m = modal_decomposition(
        np.array([0.0]), np.zeros((1, *c.shape)), l_ext[None], np.zeros((1, *c.shape)), c[None]
    )
    if pair:
        assert m.z_diff is not None
        return {"z": float(m.z_diff[0]), "eps_eff": float(np.mean(m.eps_eff[0])), "z_odd": float(m.z_odd[0]), "z_even": float(m.z_even[0])}  # type: ignore[index]
    assert m.z0 is not None
    return {"z": float(m.z0[0]), "eps_eff": float(m.eps_eff[0, 0])}


def bem_values(xs: CrossSection, scale: float, pair: bool) -> dict[str, float]:
    r = ImpedanceServiceImpl(mesh=MeshParams().scaled(scale)).solve(xs, [0.0], method="bem")
    p = r.params
    if pair:
        return {"z": float(p.z_diff.array[0]), "eps_eff": float(np.mean(p.eps_eff_modal.array[0]))}
    return {"z": float(p.z0.array[0]), "eps_eff": float(p.eps_eff_modal.array[0, 0])}


def one(sp: Spec, t_um: float, n_levels: int) -> dict[str, Any]:
    xs = build(sp, t_um)
    t0 = time.time()
    levels = rs.fem_levels(xs, n_levels)
    rows = []
    for lv in levels:
        q = quantities(lv.c, lv.l_ext, sp.pair)
        rows.append({"level": lv.level, "h_max_m": lv.h_max, "n_dofs": lv.n_dofs, **q})
    ext = {k: rs.richardson([r[k] for r in rows]) for k in rows[0] if k in ("z", "eps_eff")}
    ref = {"z": ext["z"]["value"], "eps_eff": ext["eps_eff"]["value"]}
    est = {"z_pct": 100 * ext["z"]["err"] / ref["z"], "eps_eff_pct": 100 * ext["eps_eff"]["err"] / ref["eps_eff"]}
    bem1, bem4 = bem_values(xs, 1.0, sp.pair), bem_values(xs, 4.0, sp.pair)
    pct = lambda a, b: 100.0 * (a / b - 1.0)  # noqa: E731
    return {
        "schema": "oa-bench-reference/1",
        "id": f"B-IMP-02/{sp.name}_{int(t_um)}um",
        "geometry": {**sp.__dict__, "copper_um": t_um, "etch": "w_top = w_bottom - t", "dielectric": "lossless, static eps_r"},
        "quantity": "zdiff" if sp.pair else "z0",
        "reference": ref,
        "error_estimate_pct": est,
        "convergence": rows,
        "richardson_ratio": {k: v["ratio"] for k, v in ext.items()},
        "bem_default": {**bem1, "z_err_pct": pct(bem1["z"], ref["z"]), "eps_eff_err_pct": pct(bem1["eps_eff"], ref["eps_eff"])},
        "bem_4x": {**bem4, "z_err_pct": pct(bem4["z"], ref["z"]), "eps_eff_err_pct": pct(bem4["eps_eff"], ref["eps_eff"]), "vs_default_pct": pct(bem1["z"], bem4["z"])},
        "provenance": {"fem": "oa_impedance.fem P2", "levels": n_levels, "wall_s": round(time.time() - t0, 1)},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--levels", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    for sp in GEOMS:
        for t_um in (35, 18):
            name = f"{sp.name}_{t_um}um"
            if a.only and a.only not in name:
                continue
            d = OUT / name
            f = d / "reference.json"
            if f.exists() and not a.force:
                continue
            d.mkdir(parents=True, exist_ok=True)
            doc = one(sp, float(t_um), a.levels)
            f.write_text(json.dumps(doc, indent=1), encoding="utf-8")
            print(name, doc["reference"], doc["error_estimate_pct"], doc["bem_default"]["z_err_pct"], doc["bem_default"]["eps_eff_err_pct"], doc["provenance"]["wall_s"], flush=True)


if __name__ == "__main__":
    main()
