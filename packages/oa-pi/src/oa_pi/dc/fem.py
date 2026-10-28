# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="arg-type,assignment,attr-defined,call-overload,index,return-value"
# mypy: disable-error-code="no-any-return,unused-ignore,import-untyped,union-attr,var-annotated"
"""P1 finite-element solve of the DC network (research 04 section 1.1).

Every (layer, net) sheet is meshed with ``oa_mesh.mesh_copper_sheets`` (via-barrel rings are mesh
nodes; pad outlines are mesh boundaries), the element matrices
``K_e = (b b^T + c c^T) / (4 A_e R_s)`` are assembled as one vectorized COO triplet list, and the
nodes of every ring/pin are merged into a single unknown (exact equipotential constraint).
Via-barrel resistors are stamped between the ring unknowns.

Unknown numbering has two levels: level A (one unknown per ring/pin) is where per-pin currents
are read from; level B merges the pins of a regulator/load group.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
import scipy.sparse as sp
import shapely
from shapely.ops import unary_union

from oa_mesh import Contact, MeshError, SheetMesh, SheetPiece, SizeField, mesh_copper_sheets
from oa_model.arrays import Points
from oa_model.geometry import Polygon
from oa_physics import resistivity
from oa_pi.dc.network import DcNetwork, Pin, Sheet
from oa_pi.dc.terminals import PinRecord, labels, solve_terminals
from oa_pi.errors import PiGeometryError

__all__ = ["DcSolution", "MeshedSheet", "SheetField", "mesh_network", "solve_fem"]

F64 = npt.NDArray[np.float64]
I64 = npt.NDArray[np.int64]
_TOL = 1e-9


@dataclass
class MeshedSheet:
    sheet: Sheet
    mesh: SheetMesh


@dataclass
class SheetField:
    """Per-sheet solution on its mesh."""

    sheet: Sheet
    mesh: SheetMesh
    offset: int  # global index of mesh node 0
    v: F64  # (n,) node voltage [V]
    j_mag: F64  # (m,) volume current density per triangle [A/m^2]
    k_mag: F64  # (m,) sheet current per width per triangle [A/m]
    rho: F64  # (m,) resistivity per triangle [ohm m]
    centroid: F64  # (m, 2)


PinResult = PinRecord


@dataclass
class BarrelResult:
    id: str
    net: str
    xy: tuple[float, float]
    layers: tuple[str, ...]
    currents: tuple[float, ...]  # per segment, positive top -> bottom [A]
    resistances: tuple[float, ...]
    area: float
    kind: str


@dataclass
class DcSolution:
    network: DcNetwork
    fields: list[SheetField]
    pins: list[PinRecord]
    barrels: list[BarrelResult]
    group_voltage: dict[str, float]
    i_source: float
    v_source: float  # V_P - V_N at the regulator pins [V]
    loss_sheets: float
    loss_barrels: float
    loss_source: float
    residual: float
    n_unknowns: int
    solver: str
    timings: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------------------------
# meshing
# ---------------------------------------------------------------------------------------------


def _to_model_polygon(p: Any) -> Polygon:
    return Polygon(
        exterior=Points(np.asarray(p.exterior.coords)[:-1]),
        holes=tuple(Points(np.asarray(r.coords)[:-1]) for r in p.interiors),
    )


def _polys(g: Any) -> list[Any]:
    if g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    return [q for part in g.geoms for q in _polys(part)]


def _track_fields(net: DcNetwork, board_tracks: Any, h: float) -> list[SizeField]:
    out: list[SizeField] = []
    f = net.cfg.mesh.h_trace_factor
    keys = {s.key for s in net.sheets}
    for t in board_tracks:
        key = f"{t.layer}:{t.net}"
        size = t.width * f
        if key not in keys or size >= h:
            continue
        pts: list[tuple[float, float]] = []
        for seg in t.path:  # arcs: the chord is enough to seed the refinement
            pts += [seg.start, seg.end]
        arr = np.array(pts)
        if len(arr) >= 2 and float(np.linalg.norm(arr[-1] - arr[0])) / size < 20000:
            out.append(SizeField(kind="polyline", size=size, points=Points(arr), piece_ids=(key,)))
    return out


def sheet_pins(net: DcNetwork, s: Sheet) -> list[Pin]:
    """Unique pins whose pad lies in the copper of sheet ``s`` (same layer and net)."""
    seen: dict[str, Pin] = {}
    for p in net.all_pins():
        if (
            p.layer == s.layer
            and p.net == s.net
            and p.id not in seen
            and any(
                poly.buffer(_TOL).contains(p.region.representative_point()) for poly in s.polygons
            )
        ):
            seen[p.id] = p
    return list(seen.values())


def mesh_network(net: DcNetwork, tracks: Any = ()) -> list[MeshedSheet]:
    """Mesh all sheets (pads removed, barrel rings as embedded contacts)."""
    cfg = net.cfg.mesh
    pieces: list[SheetPiece] = []
    owners: list[Sheet] = []
    fields = _track_fields(net, tracks, cfg.h_plane)
    for s in net.sheets:
        pins = sheet_pins(net, s)
        pin_ids = {p.id for p in pins}
        contacts: list[Contact] = []
        for b in net.barrels:
            if b.net != s.net or s.layer not in b.layers:
                continue
            if b.pin_ends.get(s.layer) in pin_ids:
                continue  # the barrel ends on a pad: it connects to the pin directly
            contacts.append(
                Contact(
                    id=f"b:{b.id}",
                    x=b.xy[0],
                    y=b.xy[1],
                    radius=b.r_out,
                    segments=cfg.via_ring_segments,
                    mode="embedded",
                )
            )
        copper = unary_union(s.polygons)
        if pins:
            copper = copper.difference(unary_union([p.region for p in pins]))
        polys = [p for p in _polys(copper) if p.area > 1e-12]
        if not polys:
            continue
        for p in pins:  # refine along the pad outline
            size = min(cfg.h_plane, p.region.length / cfg.via_ring_segments)
            if size < cfg.h_plane:
                n = max(int(np.ceil(p.region.length / size)), 8)
                xy = np.asarray(
                    shapely.get_coordinates(
                        shapely.line_interpolate_point(
                            p.region.exterior, np.linspace(0, p.region.length, n, endpoint=False)
                        )
                    )
                )
                fields.append(
                    SizeField(kind="points", size=size, points=Points(xy), piece_ids=(s.key,))
                )
        pieces.append(
            SheetPiece(
                id=s.key,
                layer=s.layer,
                net=s.net,
                z=s.z,
                polygons=tuple(_to_model_polygon(p) for p in polys),
                contacts=tuple(contacts),
            )
        )
        owners.append(s)
    try:
        meshes = mesh_copper_sheets(pieces, h_default=cfg.h_plane, size_fields=fields)
    except MeshError as exc:
        raise PiGeometryError(
            f"meshing failed: {exc}",
            path=getattr(exc, "path", None),
            hint=getattr(exc, "hint", None) or "Check the copper geometry near the reported item.",
        ) from exc
    return [MeshedSheet(s, m) for s, m in zip(owners, meshes, strict=True)]


# ---------------------------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------------------------


def _rho(sheet: Sheet, cen: F64, temperature: Any) -> F64:
    if callable(temperature):
        t = np.asarray(temperature(sheet.layer, cen), dtype=np.float64)
    else:
        t = np.full(len(cen), float(temperature))
    return np.asarray(resistivity(sheet.material, t), dtype=np.float64)


def _element_data(nodes: F64, tri: I64) -> tuple[F64, F64, F64, F64]:
    p = nodes[tri]  # (m, 3, 2)
    x, y = p[:, :, 0], p[:, :, 1]
    b = np.stack([y[:, 1] - y[:, 2], y[:, 2] - y[:, 0], y[:, 0] - y[:, 1]], axis=1)
    c = np.stack([x[:, 2] - x[:, 1], x[:, 0] - x[:, 2], x[:, 1] - x[:, 0]], axis=1)
    area = 0.5 * np.abs(b[:, 0] * c[:, 1] - b[:, 1] * c[:, 0])
    return b, c, area, p.mean(axis=1)


def solve_fem(
    net: DcNetwork,
    *,
    tracks: Any = (),
    meshed: list[MeshedSheet] | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> DcSolution:
    """Mesh, assemble and solve the DC network."""
    cfg = net.cfg
    tm: dict[str, float] = {}
    t0 = time.perf_counter()
    if meshed is None:
        meshed = mesh_network(net, tracks)
    tm["mesh"] = time.perf_counter() - t0
    if progress:
        progress(0.4, "meshed")
    t1 = time.perf_counter()

    offs = np.cumsum([0] + [ms.mesh.nodes.array.shape[0] for ms in meshed])
    n_mesh = int(offs[-1])
    sheet_idx = {ms.sheet.key: i for i, ms in enumerate(meshed)}
    pins_by_id: dict[str, Pin] = {p.id: p for p in net.all_pins()}
    pin_virtual = {pid: n_mesh + k for k, pid in enumerate(sorted(pins_by_id))}
    n_tot = n_mesh + len(pin_virtual)

    # ---- element matrices ---------------------------------------------------------------------
    rows: list[I64] = []
    cols: list[I64] = []
    vals: list[F64] = []
    geom: list[tuple[F64, F64, F64, F64, F64]] = []  # b, c, area, rho, rs
    for ms, off in zip(meshed, offs[:-1], strict=True):
        s, m = ms.sheet, ms.mesh
        tri = m.triangles.array.astype(np.int64)
        b, c, area, cen = _element_data(m.nodes.array, tri)
        rho = _rho(s, cen, net.temperature)
        rs = rho / s.thickness
        ke = (np.einsum("mi,mj->mij", b, b) + np.einsum("mi,mj->mij", c, c)) / (4.0 * area * rs)[
            :, None, None
        ]
        gt = tri + int(off)
        rows.append(np.repeat(gt, 3, axis=1).reshape(-1))
        cols.append(np.tile(gt, (1, 3)).reshape(-1))
        vals.append(ke.reshape(-1))
        geom.append((b, c, area, rho, rs))

    # ---- level A unknowns: rings and pins -------------------------------------------------------
    groups: list[I64] = []
    contact_nodes: dict[tuple[str, str], I64] = {}
    for ms, off in zip(meshed, offs[:-1], strict=True):
        for cn in ms.mesh.contacts:
            nodes_g = cn.nodes.array.astype(np.int64) + int(off)
            contact_nodes[(ms.sheet.key, cn.id)] = nodes_g
            groups.append(nodes_g)
    for pid, p in pins_by_id.items():
        si = sheet_idx.get(f"{p.layer}:{p.net}")
        sel = [np.array([pin_virtual[pid]], dtype=np.int64)]
        if si is not None:
            nd = meshed[si].mesh.nodes.array
            inside = np.flatnonzero(shapely.contains_xy(p.region.buffer(_TOL), nd[:, 0], nd[:, 1]))
            sel.append(inside.astype(np.int64) + int(offs[si]))
        groups.append(np.concatenate(sel))
    lab_a, n_a = labels(n_tot, groups)

    # ---- barrel stamps (level A) ----
    br_i: list[int] = []
    br_j: list[int] = []
    br_g: list[float] = []

    def barrel_dof(b: Any, lid: str) -> int:
        if lid in b.pin_ends:
            return int(lab_a[pin_virtual[b.pin_ends[lid]]])
        nodes_g = contact_nodes.get((f"{lid}:{b.net}", f"b:{b.id}"))
        if nodes_g is None:
            raise PiGeometryError(
                f"barrel {b.id} has no contact ring on {lid}",
                path=b.id,
                hint="The contact ring was not meshed; check the copper around the via.",
            )
        return int(lab_a[nodes_g[0]])

    for b in net.barrels:
        dofs = [barrel_dof(b, lid) for lid in b.layers]
        for k, r in enumerate(b.seg_r):
            br_i.append(dofs[k])
            br_j.append(dofs[k + 1])
            br_g.append(1.0 / r)

    # ---- assemble K_A ----
    gr = lab_a[np.concatenate(rows)]
    gc = lab_a[np.concatenate(cols)]
    k_a = sp.coo_matrix((np.concatenate(vals), (gr, gc)), shape=(n_a, n_a)).tocsr()
    if br_g:
        i, j, g = np.array(br_i), np.array(br_j), np.array(br_g)
        kb = sp.coo_matrix(
            (
                np.concatenate([g, g, -g, -g]),
                (np.concatenate([i, j, i, j]), np.concatenate([i, j, j, i])),
            ),
            shape=(n_a, n_a),
        ).tocsr()
        k_a = (k_a + kb).tocsr()

    pin_dof = {pid: int(lab_a[pin_virtual[pid]]) for pid in pins_by_id}
    for pid, p in pins_by_id.items():
        d = pin_dof[pid]
        if k_a.indptr[d + 1] == k_a.indptr[d]:
            raise PiGeometryError(
                f"pin {p.refdes}.{p.number} has no electrical connection to the copper",
                path=p.id,
                hint="No mesh node touches the pad and no via sits in it; reduce mesh.h_plane.",
            )
    tm["assemble"] = time.perf_counter() - t1
    if progress:
        progress(0.6, "assembled")
    t2 = time.perf_counter()
    ts = solve_terminals(net, k_a, pin_dof, cfg.mesh.solver)
    tm["solve"] = time.perf_counter() - t2
    if progress:
        progress(0.85, "solved")

    # ---- post-processing ----
    v_a = ts.v_a
    v_node = v_a[lab_a[:n_mesh]]
    fields: list[SheetField] = []
    loss_sheets = 0.0
    for ms, off, (b, c, area, rho, rs) in zip(meshed, offs[:-1], geom, strict=True):
        n_loc = ms.mesh.nodes.array.shape[0]
        vl = v_node[int(off) : int(off) + n_loc]
        tri = ms.mesh.triangles.array.astype(np.int64)
        vt = vl[tri]
        gx = (vt * b).sum(axis=1) / (2.0 * area)
        gy = (vt * c).sum(axis=1) / (2.0 * area)
        gmag = np.hypot(gx, gy)
        loss_sheets += float(np.sum(gmag**2 * area / rs))
        cen = ms.mesh.nodes.array[tri].mean(axis=1)
        fields.append(SheetField(ms.sheet, ms.mesh, int(off), vl, gmag / rho, gmag / rs, rho, cen))

    barrels: list[BarrelResult] = []
    loss_barrels = 0.0
    for br in net.barrels:
        dofs = [barrel_dof(br, lid) for lid in br.layers]
        cur = tuple(float((v_a[dofs[k]] - v_a[dofs[k + 1]]) / r) for k, r in enumerate(br.seg_r))
        loss_barrels += sum(i * i * r for i, r in zip(cur, br.seg_r, strict=True))
        barrels.append(
            BarrelResult(br.id, br.net, br.xy, br.layers, cur, br.seg_r, br.area, br.kind)
        )

    return DcSolution(
        network=net,
        fields=fields,
        pins=ts.pins,
        barrels=barrels,
        group_voltage=ts.group_voltage,
        i_source=ts.i_source,
        v_source=ts.v_source,
        loss_sheets=loss_sheets,
        loss_barrels=loss_barrels,
        loss_source=cfg.vrm.r_out * ts.i_source**2,
        residual=ts.residual,
        n_unknowns=ts.n_unknowns,
        solver=ts.solver,
        timings=tm,
    )
