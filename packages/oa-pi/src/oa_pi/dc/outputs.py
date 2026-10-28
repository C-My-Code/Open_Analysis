# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="arg-type,assignment,attr-defined,call-overload,index,return-value"
# mypy: disable-error-code="no-any-return,unused-ignore,import-untyped,union-attr,var-annotated"
"""Turn a ``DcSolution`` into tables, scalars and arrays (the content of the result items).

``summarize`` produces JSON-only data (tables, scalars, worst-case facts) plus numpy arrays for
the field pieces; ``oa_pi.dc.analysis`` stores the arrays as one artifact and rebuilds the typed
``ResultItem`` s from both in ``reduce``.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt
import shapely
from scipy.spatial import cKDTree

from oa_pi.dc.fem import DcSolution

__all__ = ["MAX_ROWS", "summarize"]

F64 = npt.NDArray[np.float64]
MAX_ROWS = 1000  # tables are inlined; larger ones are cut (worst first) and flagged


def _load_rows(sol: DcSolution) -> list[list[Any]]:
    net = sol.network
    cfg = net.cfg
    vset = cfg.vrm.vset
    limit = cfg.limits.drop_fraction * vset
    rows: list[list[Any]] = []
    for ld in net.loads:
        v_rail = sol.group_voltage[f"load:{ld.name}"]
        v_ret = sol.group_voltage.get(f"load_ret:{ld.name}", 0.0)
        v_load = v_rail - v_ret
        drop = vset - v_load
        xy = np.mean([p.xy for p in ld.rail_pins], axis=0)
        rows.append(
            [
                ld.name,
                ld.cfg.component,
                float(v_rail),
                float(v_ret),
                float(v_load),
                float(drop),
                float(drop / vset),
                float(limit),
                float(ld.cfg.current),
                "pass" if drop <= limit else "fail",
                float(xy[0]),
                float(xy[1]),
            ]
        )
    rows.sort(key=lambda r: (-r[5], r[0]))
    return rows


LOAD_COLUMNS = [
    ("load", "str", None, "id"),
    ("component", "str", None, "item"),
    ("v_rail", "f8", "V", "value"),
    ("v_return", "f8", "V", "value"),
    ("v_load", "f8", "V", "value"),
    ("drop", "f8", "V", "value"),
    ("drop_fraction", "f8", None, "value"),
    ("limit", "f8", "V", "value"),
    ("current", "f8", "A", "value"),
    ("status", "str", None, "status"),
    ("x", "f8", "m", "location_x"),
    ("y", "f8", "m", "location_y"),
]
VIA_COLUMNS = [
    ("via", "str", None, "id"),
    ("net", "str", None, "net"),
    ("from_layer", "str", None, "layer"),
    ("to_layer", "str", None, "layer"),
    ("current", "f8", "A", "value"),
    ("resistance", "f8", "ohm", "value"),
    ("density", "f8", "A/m^2", "value"),
    ("density_limit", "f8", "A/m^2", "value"),
    ("status", "str", None, "status"),
    ("x", "f8", "m", "location_x"),
    ("y", "f8", "m", "location_y"),
]
PIN_COLUMNS = [
    ("group", "str", None, "id"),
    ("role", "str", None, "item"),
    ("refdes", "str", None, "item"),
    ("pin", "str", None, "item"),
    ("net", "str", None, "net"),
    ("layer", "str", None, "layer"),
    ("voltage", "f8", "V", "value"),
    ("current", "f8", "A", "value"),
    ("x", "f8", "m", "location_x"),
    ("y", "f8", "m", "location_y"),
]


def _via_rows(sol: DcSolution) -> list[list[Any]]:
    lim = sol.network.cfg.limits.via_current_density
    rows: list[list[Any]] = []
    for b in sol.barrels:
        for k, cur in enumerate(b.currents):
            dens = abs(cur) / b.area
            rows.append(
                [
                    b.id,
                    b.net,
                    b.layers[k],
                    b.layers[k + 1],
                    float(cur),
                    float(b.resistances[k]),
                    float(dens),
                    float(lim),
                    "pass" if dens <= lim else "fail",
                    float(b.xy[0]),
                    float(b.xy[1]),
                ]
            )
    rows.sort(key=lambda r: (-abs(r[4]), r[0], r[2]))
    return rows


def _pin_rows(sol: DcSolution) -> list[list[Any]]:
    rows = [
        [
            r.group,
            r.role,
            r.pin.refdes,
            r.pin.number,
            r.pin.net,
            r.pin.layer,
            float(r.voltage),
            float(r.current),
            float(r.pin.xy[0]),
            float(r.pin.xy[1]),
        ]
        for r in sol.pins
    ]
    rows.sort(key=lambda r: (r[0], -abs(r[7]), r[2], r[3]))
    return rows


def _worst_neck(sol: DcSolution) -> dict[str, Any] | None:
    """Highest volume current density away from the contacts (a neck, not a contact spike)."""
    net = sol.network
    h = net.cfg.mesh.h_plane
    best: dict[str, Any] | None = None
    for f in sol.fields:
        if len(f.j_mag) == 0:
            continue
        excl = np.zeros(len(f.centroid), dtype=bool)
        pts = [b.xy for b in net.barrels if f.sheet.layer in b.layers and b.net == f.sheet.net]
        if pts:
            d, _ = cKDTree(np.array(pts)).query(f.centroid)
            excl |= d < max(3.0 * h, 3.0 * max(b.r_out for b in net.barrels))
        for p in net.all_pins():
            if p.layer == f.sheet.layer and p.net == f.sheet.net:
                excl |= shapely.distance(p.region, shapely.points(f.centroid)) < 3.0 * h
        j = np.where(excl, 0.0, f.j_mag)
        k = int(np.argmax(j))
        if j[k] > 0 and (best is None or j[k] > best["j"]):
            best = {
                "j": float(j[k]),
                "k": float(f.k_mag[k]),
                "layer": f.sheet.layer,
                "net": f.sheet.net,
                "x": float(f.centroid[k, 0]),
                "y": float(f.centroid[k, 1]),
            }
    return best


def summarize(sol: DcSolution) -> tuple[dict[str, Any], dict[str, F64]]:
    """JSON data (tables, scalars, worst-case facts, meta) and the field arrays."""
    net = sol.network
    cfg = net.cfg
    vset = cfg.vrm.vset
    loads = _load_rows(sol)
    vias = _via_rows(sol)
    pins = _pin_rows(sol)
    i_tot = float(sum(ld.cfg.current for ld in net.loads))
    v_load_w = float(
        sum(ld.cfg.current * r[4] for ld, r in zip(net.loads, _by_name(net, loads), strict=True))
    )
    r_eff = (sol.v_source - v_load_w / i_tot) / i_tot if i_tot > 0 else 0.0
    worst = loads[0]
    worst_via = vias[0] if vias else None
    neck = _worst_neck(sol)
    loss = sol.loss_sheets + sol.loss_barrels + sol.loss_source
    p_in = sol.v_source * sol.i_source
    p_out = float(
        sum(ld.cfg.current * r[4] for ld, r in zip(net.loads, _by_name(net, loads), strict=True))
    )
    limit_j = cfg.limits.via_current_density

    scalars: list[dict[str, Any]] = [
        _sc("vset", vset, "V"),
        _sc("load_current_total", i_tot, "A"),
        _sc("regulator_current", sol.i_source, "A"),
        _sc("regulator_voltage", sol.v_source, "V"),
        _sc("worst_load_drop", worst[5], "V", worst[9], worst[7]),
        _sc("worst_load_drop_fraction", worst[6], None, worst[9], cfg.limits.drop_fraction),
        _sc("effective_resistance", r_eff, "ohm"),
        _sc("loss_total", loss, "W"),
        _sc("loss_copper_sheets", sol.loss_sheets, "W"),
        _sc("loss_via_barrels", sol.loss_barrels, "W"),
        _sc("loss_regulator_output", sol.loss_source, "W"),
        _sc("power_balance_error", (p_in - p_out - loss) / p_in if p_in else 0.0, None),
        _sc("kcl_residual", sol.residual, None),
    ]
    if worst_via is not None:
        scalars.append(_sc("worst_via_current", abs(worst_via[4]), "A"))
        dens = max(r[6] for r in vias)
        status = "fail" if dens > limit_j else "pass"
        scalars.append(_sc("worst_via_density", dens, "A/m^2", status, limit_j))
    if neck is not None:
        scalars.append(_sc("worst_neck_current_density", neck["j"], "A/m^2"))

    arrays: dict[str, F64] = {}
    pieces: list[dict[str, Any]] = []
    for i, f in enumerate(sol.fields):
        m = f.mesh
        dv = (vset - f.v) if f.sheet.net == net.rail else f.v
        arrays[f"nodes_{i}"] = np.asarray(m.nodes.array, dtype=np.float64)
        arrays[f"tris_{i}"] = np.asarray(m.triangles.array, dtype=np.int64)
        arrays[f"v_{i}"] = f.v
        arrays[f"dv_{i}"] = dv
        arrays[f"j_{i}"] = f.j_mag
        pieces.append(
            {
                "key": f.sheet.key,
                "layer": f.sheet.layer,
                "net": f.sheet.net,
                "z": f.sheet.z,
                "n_nodes": len(f.v),
                "n_triangles": len(f.j_mag),
            }
        )

    def table(
        cols: list[tuple[str, str, str | None, str]], rows: list[list[Any]]
    ) -> dict[str, Any]:
        return {
            "columns": [list(c) for c in cols],
            "rows": [[_r8(v) for v in r] for r in rows[:MAX_ROWS]],
            "n_rows": len(rows),
            "truncated": len(rows) > MAX_ROWS,
        }

    data: dict[str, Any] = {
        "tables": {
            "loads": table(LOAD_COLUMNS, loads),
            "vias": table(VIA_COLUMNS, vias),
            "pins": table(PIN_COLUMNS, pins),
        },
        "scalars": scalars,
        "pieces": pieces,
        "worst": {
            "load": {"name": worst[0], "drop": worst[5], "fraction": worst[6], "status": worst[9]},
            "via": None
            if worst_via is None
            else {
                "id": worst_via[0],
                "current": abs(worst_via[4]),
                "density": max(r[6] for r in vias),
                "status": "fail" if any(r[8] == "fail" for r in vias) else "pass",
                "x": worst_via[9],
                "y": worst_via[10],
            },
            "neck": neck,
        },
        "meta": {
            "solver": sol.solver,
            "n_unknowns": sol.n_unknowns,
            "timings": {k: float(v) for k, v in sol.timings.items()},
            "stats": dict(net.stats.items()),
            "n_loads_failing": sum(1 for r in loads if r[9] == "fail"),
            "n_via_segments_failing": sum(1 for r in vias if r[8] == "fail"),
            "gmsh_version": sol.fields[0].mesh.gmsh_version if sol.fields else None,
        },
    }
    return data, arrays


def _r8(v: Any) -> Any:
    """Table values keep 8 significant digits (compact summaries, far below solver accuracy)."""
    return float(f"{v:.8g}") if isinstance(v, float) else v


def _by_name(net: Any, rows: list[list[Any]]) -> list[list[Any]]:
    idx = {r[0]: r for r in rows}
    return [idx[ld.name] for ld in net.loads]


def _sc(
    name: str,
    value: float,
    unit: str | None,
    status: str | None = None,
    limit: float | None = None,
) -> dict[str, Any]:
    d: dict[str, Any] = {"name": name, "value": float(value), "unit": unit}
    if status is not None:
        d["status"] = status
    if limit is not None:
        d["limit"] = float(limit)
    return d
