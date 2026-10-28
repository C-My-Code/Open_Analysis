# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Replay an ``OccModel`` plan inside a gmsh session: primitives, BooleanFragments, groups.

The model is built in millimetres (OCC's default tolerance is absolute, and 18 um copper is
1.8e-5 m, close to it) and scaled back to metres on write (``Mesh.ScalingFactor``).

After ``occ.fragment`` every volume piece knows which input primitives it came from. The piece's
class (void > copper > slab > fill > background) picks its material; copper pieces are removed
from the model (conductors are meshed as boundary surfaces, PEC or impedance walls, not as
volumes). A surface then belongs to

* a conductor, if it is a sheet or bounds a copper piece and a remaining volume;
* a port, if it descends from a port rectangle and bounds a remaining volume;
* a domain boundary face (``bc:xmin`` ...), if it bounds exactly one remaining volume and is
  neither of the above.

Surfaces that bound no remaining volume (copper-only faces, port parts inside copper, sheets
inside copper) are dropped. The groups are disjoint, so Palace never sees an attribute twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from oa_mesh.errors import MeshError
from oa_mesh.occ3d.types import (
    CLS_COPPER,
    GroupInfo,
    OccModel,
    Prim,
)

__all__ = ["MM", "Built", "construct"]

MM = 1e3
"""Metres to model units (millimetres)."""

TAG_MAT = 1
TAG_COND = 100
TAG_PORT = 1000
TAG_BC = 2000
_FACES = ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")
_TOL = 1e-6  # mm


@dataclass
class Built:
    groups: list[GroupInfo]
    volumes: dict[int, tuple[str, tuple[float, ...], int]]  # tag -> (material, bbox mm, class)
    cond_curves: dict[str, list[int]]
    warnings: list[str]
    box_volume: float
    copper_volume: float
    retried: bool = False
    surface_group: dict[int, str] = field(default_factory=dict)


def _poly_surface(gm: Any, poly: Any, z: float) -> int:
    def loop(coords: Any) -> int:
        pts = [gm.model.occ.addPoint(x * MM, y * MM, z * MM) for x, y in list(coords)[:-1]]
        lines = [gm.model.occ.addLine(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
        return int(gm.model.occ.addCurveLoop(lines))

    loops = [loop(poly.exterior.coords)] + [loop(h.coords) for h in poly.interiors]
    return int(gm.model.occ.addPlaneSurface(loops))


def _quad(gm: Any, corners: tuple[tuple[float, float, float], ...]) -> int:
    pts = [gm.model.occ.addPoint(x * MM, y * MM, z * MM) for x, y, z in corners]
    lines = [gm.model.occ.addLine(pts[i], pts[(i + 1) % 4]) for i in range(4)]
    return int(gm.model.occ.addPlaneSurface([gm.model.occ.addCurveLoop(lines)]))


def _cyl(gm: Any, p: Prim) -> int:
    occ = gm.model.occ
    cx, cy = p.center
    h = (p.z1 - p.z0) * MM
    outer = occ.addCylinder(cx * MM, cy * MM, p.z0 * MM, 0, 0, h, p.r * MM)
    if p.r_inner > 0:
        inner = occ.addCylinder(cx * MM, cy * MM, p.z0 * MM, 0, 0, h, p.r_inner * MM)
        out, _ = occ.cut([(3, outer)], [(3, inner)])
        vols = [t for d, t in out if d == 3]
        if len(vols) != 1:
            raise MeshError(
                f"via barrel {p.key}: tube cut gave {len(vols)} solids", code="MESH_GMSH_FAILED"
            )
        return int(vols[0])
    return int(outer)


def _make(gm: Any, p: Prim) -> list[tuple[int, int]]:
    occ = gm.model.occ
    if p.kind == "extrude":
        out: list[tuple[int, int]] = []
        for poly in p.polys:
            s = _poly_surface(gm, poly, p.z0)
            ex = occ.extrude([(2, s)], 0, 0, (p.z1 - p.z0) * MM)
            out.extend((3, t) for d, t in ex if d == 3)
        return out
    if p.kind == "sheet":
        return [(2, _poly_surface(gm, poly, p.z0)) for poly in p.polys]
    if p.kind == "cyl":
        return [(3, _cyl(gm, p))]
    if p.kind == "disc":
        return [
            (2, int(occ.addDisk(p.center[0] * MM, p.center[1] * MM, p.z0 * MM, p.r * MM, p.r * MM)))
        ]
    if p.kind == "rect":
        return [(2, _quad(gm, p.corners))]
    if p.kind == "annulus":
        cx, cy, z = p.center[0] * MM, p.center[1] * MM, p.z0 * MM
        a = occ.addDisk(cx, cy, z, p.r * MM, p.r * MM)
        b = occ.addDisk(cx, cy, z, p.r_inner * MM, p.r_inner * MM)
        out2, _ = occ.cut([(2, a)], [(2, b)])
        return [(d, t) for d, t in out2 if d == 2]
    raise MeshError(f"unknown primitive kind {p.kind!r}", code="MESH_SETUP")


def _fragment(
    gm: Any, model: OccModel, heal: bool
) -> tuple[dict[tuple[int, int], set[int]], list[tuple[int, int]]]:
    occ = gm.model.occ
    inputs: list[tuple[int, tuple[int, int]]] = []
    for i, p in enumerate(model.prims):
        for dt in _make(gm, p):
            inputs.append((i, dt))
    objs = [dt for _, dt in inputs]
    if heal:
        solids = [dt for dt in objs if dt[0] == 3]
        occ.healShapes(solids, model.options.heal_tolerance * MM, True, True, True, True, True)
    occ.synchronize()
    _, out_map = occ.fragment(objs, [])
    owner: dict[tuple[int, int], set[int]] = {}
    for (prim_idx, _), pieces in zip(inputs, out_map, strict=True):
        for dt in pieces:
            owner.setdefault((dt[0], dt[1]), set()).add(prim_idx)
    occ.synchronize()
    return owner, objs


def construct(gm: Any, model: OccModel) -> Built:
    """Build, fragment, classify and create physical groups. gmsh must be in a session."""
    retried = False
    try:
        owner, _ = _fragment(gm, model, heal=False)
        _check(gm)
    except Exception as exc:
        # tolerant retry: fresh model, OCC healing before the boolean
        gm.clear()
        gm.model.add("oa_mesh_retry")
        retried = True
        try:
            owner, _ = _fragment(gm, model, heal=True)
            _check(gm)
        except Exception as exc2:
            raise MeshError(
                f"BooleanFragments failed even after healing: {exc2}",
                code="MESH_GMSH_FAILED",
                hint=f"first failure: {exc}",
            ) from exc2
    built = _classify(gm, model, owner)
    built.retried = retried
    return built


def _check(gm: Any) -> None:
    if not gm.model.getEntities(3):
        raise MeshError("fragment produced no volumes", code="MESH_GMSH_FAILED")


def _classify(gm: Any, model: OccModel, owner: dict[tuple[int, int], set[int]]) -> Built:
    occ = gm.model.occ
    prims = model.prims
    warnings: list[str] = []
    vol_cls: dict[int, int] = {}
    vol_mat: dict[int, str] = {}
    vol_copper_key: dict[int, str] = {}
    for _, v in gm.model.getEntities(3):
        parents = sorted(owner.get((3, v), ()))
        vparents = [i for i in parents if prims[i].kind in ("extrude", "cyl")]
        if not vparents:
            raise MeshError(f"volume {v} has no parent primitive", code="MESH_GMSH_FAILED")
        best = min(vparents, key=lambda i: (prims[i].cls, i))
        vol_cls[v] = prims[best].cls
        if prims[best].cls == CLS_COPPER:
            vol_copper_key[v] = prims[best].key
        else:
            vol_mat[v] = prims[best].material or ""
    copper_vols = [v for v, c in vol_cls.items() if c == CLS_COPPER]
    box_volume = sum(occ.getMass(3, v) for v in vol_cls) / MM**3
    copper_volume = sum(occ.getMass(3, v) for v in copper_vols) / MM**3

    sheet_key: dict[int, str] = {}
    port_name: dict[int, str] = {}
    cond_surf: dict[int, str] = {}
    surf_up: dict[int, list[int]] = {}
    for _, s in gm.model.getEntities(2):
        up, _down = gm.model.getAdjacencies(2, s)
        surf_up[s] = [int(u) for u in up]
        for i in sorted(owner.get((2, s), ())):
            p = prims[i]
            if p.kind in ("sheet", "disc"):
                sheet_key.setdefault(s, p.key)
            elif p.kind in ("rect", "annulus"):
                port_name.setdefault(s, p.key)
    orphans: list[int] = []
    ext: list[int] = []
    for s, up in surf_up.items():
        keep = [v for v in up if vol_cls[v] != CLS_COPPER]
        cop = [v for v in up if vol_cls[v] == CLS_COPPER]
        if not keep:
            orphans.append(s)
            continue
        if s in sheet_key:
            cond_surf[s] = sheet_key[s]
            if s in port_name:
                warnings.append(f"surface {s}: sheet coincides with port {port_name[s]}")
                del port_name[s]
        elif s in port_name:
            pass
        elif cop:
            cond_surf[s] = (
                vol_copper_key[min(cop)] if len(cop) == 1 else min(vol_copper_key[c] for c in cop)
            )
        elif len(keep) == 1:
            ext.append(s)
    for s in list(port_name):
        if s in orphans:
            del port_name[s]
    # exterior surfaces get their face by location
    x0, y0, z0, x1, y1, z1 = (b * MM for b in model.bounds)
    dom = {
        "xmin": (0, x0),
        "xmax": (0, x1),
        "ymin": (1, y0),
        "ymax": (1, y1),
        "zmin": (2, z0),
        "zmax": (2, z1),
    }
    bc_surf: dict[str, list[int]] = {f: [] for f in _FACES}
    for s in ext:
        bb = gm.model.getBoundingBox(2, s)
        lo, hi = bb[:3], bb[3:]
        for f in _FACES:
            ax, val = dom[f]
            if abs(lo[ax] - val) < _TOL and abs(hi[ax] - val) < _TOL:
                bc_surf[f].append(s)
                break
        else:
            raise MeshError(
                f"exterior surface {s} lies on no domain face (bbox {bb})",
                code="MESH_GMSH_FAILED",
                hint="a conductor or port pokes out of the bounding box",
            )

    areas = {s: occ.getMass(2, s) / MM**2 for s in surf_up}
    vmeasure = {v: occ.getMass(3, v) / MM**3 for v in vol_cls}
    vol_info = {
        v: (vol_mat[v], tuple(gm.model.getBoundingBox(3, v)), vol_cls[v])
        for v in vol_cls
        if v in vol_mat
    }

    # drop copper volumes, then orphan surfaces / curves / points
    gm.model.removeEntities([(3, v) for v in copper_vols], False)
    gm.model.removeEntities([(2, s) for s in orphans], False)
    for dim in (1, 0):
        dead = [
            (dim, t)
            for _, t in gm.model.getEntities(dim)
            if not gm.model.getAdjacencies(dim, t)[0].size
        ]
        gm.model.removeEntities(dead, False)

    # --- physical groups
    groups: list[GroupInfo] = []
    surface_group: dict[int, str] = {}

    def add(
        name: str, dim: int, tag: int, kind: Any, mid: str, tags: list[int], meas: float, **kw: Any
    ) -> None:
        if not tags:
            return
        gm.model.addPhysicalGroup(dim, sorted(tags), tag, name)
        groups.append(
            GroupInfo(
                name=name,
                dim=dim,
                tag=tag,
                kind=kind,
                model_id=mid,
                n_entities=len(tags),
                measure=meas,
                **kw,
            )
        )
        if dim == 2:
            for s in tags:
                surface_group[s] = name

    mats = sorted(set(vol_mat.values()))
    for k, m in enumerate(mats):
        vs = [v for v, mm in vol_mat.items() if mm == m]
        add(f"mat:{m}", 3, TAG_MAT + k, "material", m, vs, sum(vmeasure[v] for v in vs))
    cond_curves: dict[str, list[int]] = {}
    for k, ci in enumerate(model.conductors):
        ss = [s for s, key in cond_surf.items() if key == ci.key]
        if not ss:
            warnings.append(f"conductor {ci.key} has no surface in the meshed domain")
            continue
        prefix = "cond" if ci.kind == "conductor" else "via"
        add(
            f"{prefix}:{ci.key}",
            2,
            TAG_COND + k,
            ci.kind,
            ci.key,
            ss,
            sum(areas[s] for s in ss),
            net=ci.net,
            role=ci.role,
            representation=ci.representation,
        )
        cs: dict[int, None] = {}
        for _, c in gm.model.getBoundary([(2, s) for s in ss], False, False, False):
            cs[abs(int(c))] = None
        cond_curves[ci.key] = list(cs)
    for k, pi in enumerate(model.ports):
        ss = [s for s, n in port_name.items() if n == pi.name]
        if not ss:
            warnings.append(f"port {pi.name} has no surface in the meshed domain")
            continue
        add(f"port:{pi.name}", 2, TAG_PORT + k, "port", pi.name, ss, sum(areas[s] for s in ss))
    for k, f in enumerate(_FACES):
        if bc_surf[f]:
            add(
                f"bc:{f}",
                2,
                TAG_BC + k,
                "boundary",
                f,
                bc_surf[f],
                sum(areas[s] for s in bc_surf[f]),
            )

    return Built(
        groups=groups,
        volumes=vol_info,
        cond_curves=cond_curves,
        warnings=warnings,
        box_volume=box_volume,
        copper_volume=copper_volume,
        surface_group=surface_group,
    )
