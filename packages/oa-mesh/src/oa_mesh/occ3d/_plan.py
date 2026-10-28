# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Structure3D -> list of OCC primitives (pure Python, no gmsh).

Z layering. ``Structure3D.slabs`` only list dielectric layers, so the z range of a copper layer is
a gap between slabs. This module decides what fills it:

* solid copper layer between two slabs: a ``fill`` prism of the lower slab's material (resin
  between the traces), the copper is carved out later;
* solid copper layer with a slab on one side only (outer layer): background (air);
* sheet layer (every body zero-thickness): the lower slab is extended over the gap so no thin
  volume appears; the sheet sits on the dielectric face (outer top layer: bottom face of the
  copper; otherwise its top face). An outer bottom layer whose sheet is the domain floor clips
  the domain at the sheet.

Everything is snapped to the 1 nm grid so coincident faces are exactly coincident.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import shapely
from shapely.geometry import Polygon as ShPolygon
from shapely.geometry import box as sh_box

from oa_mesh._clean import clean_polygons, polygonal_parts
from oa_mesh.errors import MeshError
from oa_mesh.occ3d.types import (
    CLS_BACKGROUND,
    CLS_COPPER,
    CLS_FILL,
    CLS_SLAB,
    CLS_VOID,
    ConductorInfo,
    OccModel,
    OccOptions,
    PortInfo,
    Prim,
)
from oa_model.em import (
    Boundaries,
    EMCoaxPort,
    EMLumpedPort,
    EMPort,
    EMWavePort,
    Structure3D,
)
from oa_model.materials import DielectricMaterial

__all__ = ["make_plan", "snap"]

_NM = 1e9


def snap(v: float) -> float:
    """Round to the 1 nm grid."""
    return round(v * _NM) / _NM


def _rect_poly(x0: float, y0: float, x1: float, y1: float) -> Any:
    return sh_box(x0, y0, x1, y1)


def _snap_geom(g: Any) -> Any:
    return shapely.set_precision(g, 1e-9) if not g.is_empty else g


def _clip(polys: Sequence[Any], rect: Any) -> list[Any]:
    out: list[Any] = []
    for p in polys:
        g = _snap_geom(p.intersection(rect))
        out.extend(polygonal_parts(g))
    return out


def _min_width(polys: Sequence[Any]) -> float:
    """Width estimate of the thinnest polygon, from its outer ring (holes ignored, so a plane
    with antipads is wide): the smaller of twice area over perimeter (a strip gives w) and the
    short side of the minimum rotated rectangle."""
    ws = []
    for p in polys:
        shell = ShPolygon(p.exterior)
        if shell.length <= 0:
            continue
        r = shell.minimum_rotated_rectangle.exterior.coords  # type: ignore[attr-defined]
        s1 = math.hypot(r[1][0] - r[0][0], r[1][1] - r[0][1])
        s2 = math.hypot(r[2][0] - r[1][0], r[2][1] - r[1][1])
        ws.append(min(2.0 * shell.area / shell.length, s1, s2))
    return min(ws) if ws else 0.0


def _lumped_corners(port: EMLumpedPort) -> tuple[tuple[float, float, float], ...]:
    p, n = port.positive, port.negative
    x0, y0, z0 = n.position[0], n.position[1], n.z
    x1, y1, z1 = p.position[0], p.position[1], p.z
    dx, dy = x1 - x0, y1 - y0
    w = port.width
    if math.hypot(dx, dy) < 1e-12:
        # vertical port: plane normal to x unless the contact patch is extended along x only
        ex, ey = p.extent
        if ex > 0 and ey == 0:
            ux, uy = 1.0, 0.0
        else:
            ux, uy = 0.0, 1.0
    else:
        # tilted/horizontal port: width runs perpendicular to the in-plane direction
        d = math.hypot(dx, dy)
        ux, uy = -dy / d, dx / d
    h = w / 2.0
    return (
        (x0 - ux * h, y0 - uy * h, z0),
        (x0 + ux * h, y0 + uy * h, z0),
        (x1 + ux * h, y1 + uy * h, z1),
        (x1 - ux * h, y1 - uy * h, z1),
    )


def _wave_corners(port: EMWavePort, b: Sequence[float]) -> tuple[tuple[float, float, float], ...]:
    u0, u1, z0, z1 = port.rect
    xmin, ymin, _, xmax, ymax, _ = b
    if port.face == "xmin":
        pts = [(xmin, u0, z0), (xmin, u1, z0), (xmin, u1, z1), (xmin, u0, z1)]
    elif port.face == "xmax":
        pts = [(xmax, u0, z0), (xmax, u1, z0), (xmax, u1, z1), (xmax, u0, z1)]
    elif port.face == "ymin":
        pts = [(u0, ymin, z0), (u1, ymin, z0), (u1, ymin, z1), (u0, ymin, z1)]
    else:
        pts = [(u0, ymax, z0), (u1, ymax, z0), (u1, ymax, z1), (u0, ymax, z1)]
    return tuple(pts)


def make_plan(
    structure: Structure3D,
    *,
    sheet_threshold: float,
    ports: Sequence[EMPort],
    options: OccOptions,
    boundaries: Boundaries | None = None,
) -> OccModel:
    """Resolve ``structure`` into primitives. Raises ``MeshError`` on unusable input."""
    warnings: list[str] = []
    bb = structure.bbox
    x0, y0, x1, y1 = snap(bb.xmin), snap(bb.ymin), snap(bb.xmax), snap(bb.ymax)
    z_lo, z_hi = snap(bb.zmin), snap(bb.zmax)
    if not (x1 > x0 and y1 > y0 and z_hi > z_lo):
        raise MeshError("bbox has no volume", code="MESH_SETUP", path="structure.bbox")
    rect = _rect_poly(x0, y0, x1, y1)
    mats = {m.name: m for m in structure.materials}
    used: dict[str, Any] = {}

    def need_material(name: str, path: str) -> None:
        if name == structure.background:
            used.setdefault(name, None)
            return
        m = mats.get(name)
        if m is None:
            raise MeshError(
                f"unknown material {name!r}",
                code="MESH_SETUP",
                path=path,
                hint="add it to Structure3D.materials",
            )
        used.setdefault(name, m.model if isinstance(m, DielectricMaterial) else None)

    # --- slabs (mutable z1 for sheet-layer extension)
    slabs: list[dict[str, Any]] = []
    for i, s in enumerate(structure.slabs):
        need_material(s.material, f"slabs[{i}].material")
        zb, zt = max(snap(s.z_bottom), z_lo), min(snap(s.z_top), z_hi)
        if zt <= zb:
            continue
        if s.footprint is None:
            polys = [rect]
        else:
            cleaned, _ = clean_polygons([s.footprint], options.clean, path=f"slabs[{i}].footprint")
            polys = _clip(cleaned, rect)
        slabs.append({"i": i, "material": s.material, "z0": zb, "z1": zt, "polys": polys})
    slabs.sort(key=lambda d: (d["z0"], d["i"]))

    # --- conductors: clean, clip, representation
    bodies: list[dict[str, Any]] = []
    for i, c in enumerate(structure.conductors):
        path = f"conductors[{i}]"
        try:
            cleaned, rep = clean_polygons(c.polygons, options.clean, path=path)
        except MeshError as exc:
            if exc.code != "MESH_EMPTY_GEOMETRY":
                raise
            warnings.append(f"{path} ({c.id}): thinner than the healing size, dropped")
            continue
        polys = _clip(cleaned, rect)
        if not polys:
            warnings.append(f"{path} ({c.id}): outside the bounding box, dropped")
            continue
        zb, zt = snap(c.z_bottom), snap(c.z_top)
        thick = zt - zb
        sheet = c.representation == "sheet" or (
            c.representation == "auto" and thick < sheet_threshold
        )
        if thick <= 0:
            sheet = True
        for w in rep.warnings:
            warnings.append(f"{path}: {w}")
        bodies.append(
            {"c": c, "polys": polys, "z0": zb, "z1": zt, "sheet": sheet, "width": _min_width(polys)}
        )

    # --- copper layers (by z range)
    layers: dict[tuple[float, float], list[dict[str, Any]]] = {}
    for b in bodies:
        layers.setdefault((b["z0"], b["z1"]), []).append(b)

    prims: list[Prim] = []
    layer_sheet_z: dict[tuple[float, float], float | None] = {}
    for (zb, zt), group in sorted(layers.items()):
        below = next((s for s in slabs if abs(s["z1"] - zb) < 1e-12), None)
        above = next((s for s in slabs if abs(s["z0"] - zt) < 1e-12), None)
        is_sheet = all(b["sheet"] for b in group)
        if is_sheet:
            if below is not None and above is not None:
                below["z1"] = zt
                z = zt
                # a slab that is just the resin of this copper layer is absorbed by the extension
                slabs[:] = [
                    q
                    for q in slabs
                    if not (
                        q is not below
                        and abs(q["z0"] - zb) < 1e-12
                        and abs(q["z1"] - zt) < 1e-12
                        and q["material"] == below["material"]
                    )
                ]
            elif below is not None:
                z = zb
                if z_hi <= zt:
                    z_hi = zb
            else:
                z = zt
                if above is not None and z_lo >= zb:
                    z_lo = zt
            layer_sheet_z[(zb, zt)] = z
        else:
            layer_sheet_z[(zb, zt)] = None
            if below is not None and above is not None:
                prims.append(
                    Prim("extrude", f"fill:{zb:.9f}", CLS_FILL, below["material"], (rect,), zb, zt)
                )

    # slab prims (after extension)
    for sl in slabs:
        z0s, z1s = max(sl["z0"], z_lo), min(sl["z1"], z_hi)
        if z1s <= z0s:
            continue
        for k, p in enumerate(sl["polys"]):
            prims.append(
                Prim("extrude", f"slab:{sl['i']}:{k}", CLS_SLAB, sl["material"], (p,), z0s, z1s)
            )

    conductors: list[ConductorInfo] = []
    for b in bodies:
        c = b["c"]
        key = (b["z0"], b["z1"])
        sz = layer_sheet_z[key]
        if sz is not None or b["sheet"]:
            z = sz if sz is not None else b["z1"]
            prims.append(Prim("sheet", c.id, CLS_COPPER, polys=tuple(b["polys"]), z0=z))
            rep_name = "sheet"
        else:
            prims.append(
                Prim("extrude", c.id, CLS_COPPER, polys=tuple(b["polys"]), z0=b["z0"], z1=b["z1"])
            )
            rep_name = "solid"
        need_material(c.material, f"conductors[{c.id}].material")
        conductors.append(ConductorInfo(c.id, "conductor", c.net, c.role, rep_name, b["width"]))

    # --- vias
    for i, v in enumerate(structure.vias):
        cx, cy = snap(v.center[0]), snap(v.center[1])
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            warnings.append(f"vias[{i}] ({v.id}): centre outside the bounding box, dropped")
            continue
        vz0, vz1 = max(snap(v.z_bottom), z_lo), min(snap(v.z_top), z_hi)
        if vz1 <= vz0:
            warnings.append(f"vias[{i}] ({v.id}): outside the z range, dropped")
            continue
        r = snap(v.r_outer)
        solid = v.plating >= v.r_outer or v.fill_material is not None
        r_in = 0.0 if solid else snap(v.r_outer - v.plating)
        prims.append(
            Prim("cyl", v.id, CLS_COPPER, center=(cx, cy), r=r, r_inner=r_in, z0=vz0, z1=vz1)
        )
        conductors.append(ConductorInfo(v.id, "via", v.net, v.role, "solid", 0.0, radius=r))
        if options.backdrill_clearance is not None:
            rv = snap(v.r_outer + options.backdrill_clearance)
            if vz0 > z_lo + 1e-12:
                prims.append(
                    Prim(
                        "cyl",
                        f"{v.id}:bd_bottom",
                        CLS_VOID,
                        structure.background,
                        center=(cx, cy),
                        r=rv,
                        z0=z_lo,
                        z1=vz0,
                    )
                )
            if vz1 < z_hi - 1e-12:
                prims.append(
                    Prim(
                        "cyl",
                        f"{v.id}:bd_top",
                        CLS_VOID,
                        structure.background,
                        center=(cx, cy),
                        r=rv,
                        z0=vz1,
                        z1=z_hi,
                    )
                )

    # --- ports
    port_infos: list[PortInfo] = []
    bounds = (x0, y0, z_lo, x1, y1, z_hi)
    seen: set[str] = set()
    for p in ports:
        if p.name in seen:
            raise MeshError(f"duplicate port name {p.name!r}", code="MESH_SETUP", path="ports")
        seen.add(p.name)
        if isinstance(p, EMWavePort):
            corners = tuple((snap(a), snap(b), snap(c)) for a, b, c in _wave_corners(p, bounds))
            prims.append(Prim("rect", p.name, CLS_VOID, corners=corners))
            port_infos.append(PortInfo(p.name, "wave", p.name))
        elif isinstance(p, EMLumpedPort):
            corners = tuple((snap(a), snap(b), snap(c)) for a, b, c in _lumped_corners(p))
            prims.append(Prim("rect", p.name, CLS_VOID, corners=corners))
            port_infos.append(PortInfo(p.name, "lumped", p.name))
        elif isinstance(p, EMCoaxPort):
            prims.append(
                Prim(
                    "annulus",
                    p.name,
                    CLS_VOID,
                    center=(snap(p.center[0]), snap(p.center[1])),
                    r=snap(p.r_outer),
                    r_inner=snap(p.r_inner),
                    z0=snap(p.z),
                )
            )
            port_infos.append(PortInfo(p.name, "coax", p.name))

    if structure.lumped:
        warnings.append(
            f"{len(structure.lumped)} lumped element(s) are not meshed; map them to lumped ports"
        )

    # --- background box (lowest priority; its pieces outside everything else are the air)
    prims.insert(
        0,
        Prim("extrude", "background", CLS_BACKGROUND, structure.background, (rect,), z_lo, z_hi),
    )
    bounds = (x0, y0, z_lo, x1, y1, z_hi)
    bc: dict[str, str] = {}
    if boundaries is not None:
        for f in ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax"):
            bc[f] = str(getattr(boundaries, f))
    return OccModel(
        structure_key=structure.key(),
        options=options,
        bounds=bounds,
        prims=tuple(prims),
        conductors=tuple(conductors),
        ports=tuple(port_infos),
        material_models=used,
        boundary_bc=bc,
        warnings=tuple(warnings),
        copper_thicknesses=tuple(sorted({round(zt - zb, 9) for (zb, zt) in layers})),
    )


def polygon_area(p: ShPolygon) -> float:
    return float(p.area)
