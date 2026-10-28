# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``build_occ_model`` and ``write_msh``: the 3D OCC mesher (WP-206)."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np

from oa_mesh._session import gmsh_session, gmsh_version
from oa_mesh.errors import MeshError
from oa_mesh.occ3d._build import MM, Built, construct
from oa_mesh.occ3d._plan import make_plan
from oa_mesh.occ3d.types import CLS_SLAB, MeshStats, OccModel, OccOptions, OccReport
from oa_mesh.types import mesh_digest
from oa_model.em import Boundaries, EMPort, EMProblem, MeshSettings, Structure3D

__all__ = ["build_occ_model", "mesh_problem", "write_msh"]

C0 = 299_792_458.0
_CELLS = {"coarse": 6.0, "medium": 10.0, "fine": 15.0, "expert": 10.0}


def _report(model: OccModel, built: Built) -> OccReport:
    dom = sum(m for g in built.groups if g.dim == 3 for m in [g.measure])
    return OccReport(
        groups=tuple(sorted(built.groups, key=lambda g: g.tag)),
        n_volumes=len(built.volumes),
        domain_volume=dom,
        box_volume=built.box_volume,
        copper_volume=built.copper_volume,
        warnings=tuple(model.warnings) + tuple(built.warnings),
        retried_with_healing=built.retried,
    )


def build_occ_model(
    structure: Structure3D,
    *,
    sheet_threshold: float,
    ports: Sequence[EMPort] = (),
    options: OccOptions | None = None,
    boundaries: Boundaries | None = None,
) -> OccModel:
    """Plan the OCC model of ``structure`` and validate it with a full BooleanFragments build.

    ``sheet_threshold`` [m]: conductors with ``representation="auto"`` thinner than this become
    zero-thickness sheets. ``boundaries`` (optional) records the BC type per domain face in the
    group sidecar. The result's ``report`` lists the physical groups; ``write_msh`` replays the
    plan, meshes and writes it.
    """
    from dataclasses import replace

    model = make_plan(
        structure,
        sheet_threshold=sheet_threshold,
        ports=ports,
        options=options or OccOptions(),
        boundaries=boundaries,
    )
    with gmsh_session() as gm:
        _configure_geometry(gm)
        try:
            built = construct(gm, model)
        except MeshError:
            raise
        except Exception as exc:
            raise MeshError(f"OCC model build failed: {exc}", code="MESH_GMSH_FAILED") from exc
        report = _report(model, built)
    return replace(model, report=report)


def mesh_problem(
    problem: EMProblem,
    path: Path,
    *,
    sheet_threshold: float | None = None,
    options: OccOptions | None = None,
) -> tuple[OccModel, MeshStats]:
    """Convenience: model + mesh of an ``EMProblem`` using its frequency plan, ports, BCs, mesh."""
    opts = (options or OccOptions()).model_copy(update={"f_max": problem.frequency.f_max})
    if sheet_threshold is None:
        # copper thinner than half the thinnest dielectric is a sheet (a thin solid costs cells)
        thick = [s.z_top - s.z_bottom for s in problem.structure.slabs]
        sheet_threshold = 0.5 * min(thick) if thick else 0.0
    model = build_occ_model(
        problem.structure,
        sheet_threshold=sheet_threshold,
        ports=problem.ports,
        options=opts,
        boundaries=problem.boundaries,
    )
    return model, write_msh(model, path, sizes=problem.mesh)


def _configure_geometry(gm: Any) -> None:
    gm.option.setNumber("Geometry.Tolerance", 1e-9)
    gm.option.setNumber("Geometry.OCCFixSmallEdges", 1)
    gm.option.setNumber("Geometry.OCCFixSmallFaces", 1)
    gm.option.setNumber("Geometry.OCCSewFaces", 1)
    gm.option.setNumber("Geometry.OCCParallel", 0)


def _eps_r(model: OccModel, material: str, f: float) -> float:
    dm = model.material_models.get(material)
    if dm is None:
        return 1.0
    from oa_physics import eps_complex

    return max(1.0, float(np.real(eps_complex(dm, [f])[0])))


def _apply_sizes(
    gm: Any,
    model: OccModel,
    built: Built,
    sizes: MeshSettings,
    f_max: float,
) -> tuple[float, float]:
    """Point sizes per volume (lambda/N, thin dielectrics) + edge/barrel fields. Returns mm."""
    opts = model.options
    n_cells = float(
        sizes.expert.get("gmsh.cells_per_wavelength", _CELLS[sizes.preset])  # type: ignore[arg-type]
    )
    grading = sizes.max_grading
    min_cell = (sizes.min_cell or 0.0) * MM
    h_cap = float(sizes.expert.get("gmsh.h_max", 0.0)) * MM  # type: ignore[arg-type]
    pt: dict[int, float] = {}
    h_vol_max = 0.0
    for v, (mat, bb, cls) in sorted(built.volumes.items()):
        lam = C0 / (f_max * math.sqrt(_eps_r(model, mat, f_max))) * MM
        h = lam / n_cells
        if h_cap > 0:
            h = min(h, h_cap)
        dz = bb[5] - bb[2]
        if cls == CLS_SLAB and 0 < dz < 2.0 * h and not _is_copper_layer(model, dz / MM):
            h = min(h, dz / opts.thin_elements)
        h = max(h, min_cell)
        h_vol_max = max(h_vol_max, h)
        for _, p in gm.model.getBoundary([(3, v)], False, False, True):
            pt[p] = min(pt.get(p, h), h)
    for p, h in sorted(pt.items()):
        gm.model.mesh.setSize([(0, p)], h)
    h_min = min(pt.values()) if pt else h_vol_max

    fields: list[int] = []
    fld = gm.model.mesh.field
    info = {c.key: c for c in model.conductors}
    for key, curves in sorted(built.cond_curves.items()):
        ci = info[key]
        if ci.kind == "via":
            h_e = 2.0 * math.pi * ci.radius * MM / opts.barrel_segments
        else:
            h_e = ci.width * MM / opts.edge_divisions
        h_e = max(h_e, min_cell, 1e-6)
        if h_e >= 0.9 * h_vol_max or not curves:
            continue
        h_min = min(h_min, h_e)
        length = max(gm.model.occ.getMass(1, c) for c in curves)
        d = fld.add("Distance")
        fld.setNumbers(d, "CurvesList", curves)
        fld.setNumber(d, "Sampling", int(min(max(math.ceil(2.0 * length / h_e), 20), 4000)))
        t = fld.add("Threshold")
        fld.setNumber(t, "InField", d)
        fld.setNumber(t, "SizeMin", h_e)
        fld.setNumber(t, "SizeMax", h_vol_max)
        fld.setNumber(t, "DistMin", h_e)
        fld.setNumber(t, "DistMax", h_e + (h_vol_max - h_e) / max(grading - 1.0, 0.05))
        fields.append(t)
    if fields:
        m = fld.add("Min")
        fld.setNumbers(m, "FieldsList", fields)
        fld.setAsBackgroundMesh(m)
    gm.option.setNumber("Mesh.MeshSizeMax", h_vol_max)
    gm.option.setNumber("Mesh.MeshSizeMin", min_cell)
    gm.option.setNumber("Mesh.MeshSizeFromPoints", 1)
    gm.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 1)
    gm.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
    return h_vol_max / MM, h_min / MM


def _is_copper_layer(model: OccModel, dz: float) -> bool:
    """Resin filling a copper layer is as thin as the copper; its size follows the traces."""
    return any(abs(dz - t) < 2e-9 for t in model.copper_thicknesses)


def _tets_of(gm: Any, vol: int) -> np.ndarray:
    types, _tags, nodes = gm.model.mesh.getElements(3, vol)
    for t, nn in zip(types, nodes, strict=True):
        if t == 4:
            return np.asarray(nn, dtype=np.int64).reshape(-1, 4)
    return np.zeros((0, 4), dtype=np.int64)


def _unique_rows(a: np.ndarray) -> int:
    return int(np.unique(np.sort(a, axis=1), axis=0).shape[0]) if len(a) else 0


def write_msh(
    model: OccModel,
    path: Path,
    *,
    sizes: MeshSettings | None = None,
    f_max: float | None = None,
    msh_version: Literal["4.1", "2.2"] = "4.1",
) -> MeshStats:
    """Replay ``model`` in a gmsh session, size it, mesh it in 3D and write ``path``.

    Sizes: lambda/N at ``f_max`` in each dielectric (N = 6/10/15 for coarse/medium/fine), at most
    half the thickness of thin dielectrics, conductor edges width/4, via barrels >= 12 segments,
    grading ``sizes.max_grading``. Writes ``path`` (gmsh .msh, metres, physical tag = attribute)
    and ``path`` with suffix ``.groups.json`` (group name -> model id, tag, BC). Deterministic for
    a fixed gmsh version.
    """
    t0 = time.perf_counter()
    sizes = sizes or MeshSettings()
    fmax = f_max if f_max is not None else model.options.f_max
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gmsh_session() as gm:
        _configure_geometry(gm)
        try:
            built = construct(gm, model)
            h_max, h_min = _apply_sizes(gm, model, built, sizes, fmax)
            try:
                gm.model.mesh.generate(3)
            except Exception:
                gm.model.mesh.clear()
                gm.option.setNumber("Mesh.Algorithm3D", 1)
                gm.model.mesh.generate(3)
        except MeshError:
            raise
        except Exception as exc:
            raise MeshError(f"gmsh failed: {exc}", code="MESH_GMSH_FAILED") from exc
        stats = _stats_and_write(gm, model, built, path, msh_version, h_max, h_min)
    report = _report(model, built)
    side = path.with_suffix(".groups.json")
    side.write_text(_sidecar(model, report, stats), encoding="utf-8")
    return stats.model_copy(update={"elapsed_s": time.perf_counter() - t0})


def _sidecar(model: OccModel, report: OccReport, stats: MeshStats) -> str:
    doc = {
        "schema": "oa-occ-groups/1",
        "structure_key": model.structure_key,
        "length_unit_m": 1.0,
        "msh_version": stats.msh_version,
        "boundary_bc": model.boundary_bc,
        "groups": [g.model_dump(mode="json") for g in report.groups],
        "ports": [{"name": p.name, "kind": p.kind, "group": f"port:{p.name}"} for p in model.ports],
        "content_hash": stats.content_hash,
    }
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def _stats_and_write(
    gm: Any,
    model: OccModel,
    built: Built,
    path: Path,
    version: str,
    h_max: float,
    h_min: float,
) -> MeshStats:
    nt, nc, _ = gm.model.mesh.getNodes()
    nodes_all = np.asarray(nc, dtype=np.float64).reshape(-1, 3) / MM
    order = np.argsort(nt)
    tag_sorted = np.asarray(nt, dtype=np.int64)[order]
    xyz = nodes_all[order]

    def idx(node_tags: np.ndarray) -> np.ndarray:
        return np.searchsorted(tag_sorted, node_tags)

    tets_parts: list[np.ndarray] = []
    vol_by_group: dict[str, float] = {}
    for v, (mat, _bb, _cls) in sorted(built.volumes.items()):
        t = _tets_of(gm, v)
        if not len(t):
            continue
        ti = idx(t)
        tets_parts.append(ti)
        vol_by_group[f"mat:{mat}"] = vol_by_group.get(f"mat:{mat}", 0.0) + float(
            np.abs(_signed_volumes(xyz, ti)).sum()
        )
    if not tets_parts:
        raise MeshError("gmsh produced no tetrahedra", code="MESH_GMSH_FAILED")
    tets = np.concatenate(tets_parts)
    vols = np.abs(_signed_volumes(xyz, tets))
    tris = 0
    used = set(np.unique(tets).tolist())
    for s in sorted(built.surface_group):
        types, _tg, nn = gm.model.mesh.getElements(2, s)
        for ty, nodes in zip(types, nn, strict=True):
            if ty == 2:
                arr = idx(np.asarray(nodes, dtype=np.int64)).reshape(-1, 3)
                tris += len(arr)
                used.update(np.unique(arr).tolist())

    all_tets = gm.model.mesh.getElementsByType(4)[0]
    sicn = np.asarray(gm.model.mesh.getElementQualities(all_tets, "minSICN"))
    gamma = np.asarray(gm.model.mesh.getElementQualities(all_tets, "gamma"))
    e_pairs = np.concatenate(
        [tets[:, [a, b]] for a, b in ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))]
    )
    n_edges = _unique_rows(e_pairs)
    f_trip = np.concatenate(
        [tets[:, [a, b, c]] for a, b, c in ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3))]
    )
    n_faces = _unique_rows(f_trip)

    gm.option.setNumber("Mesh.MshFileVersion", float(version))
    gm.option.setNumber("Mesh.Binary", 0)
    gm.option.setNumber("Mesh.SaveAll", 0)
    gm.option.setNumber("Mesh.ScalingFactor", 1.0 / MM)
    gm.write(str(path))

    h = mesh_digest(xyz, tets, 1e-9)
    return MeshStats(
        path=str(path),
        msh_version=version,
        gmsh_version=gmsh_version(),
        n_nodes=len(used),
        n_tets=len(tets),
        n_triangles=tris,
        n_edges=n_edges,
        n_faces=n_faces,
        dof_p1=n_edges,
        dof_p2=2 * n_edges + 2 * n_faces,
        min_sicn=float(sicn.min()),
        min_gamma=float(gamma.min()),
        min_volume=float(vols.min()),
        tet_volume=float(vols.sum()),
        volume_by_group=vol_by_group,
        h_max=h_max,
        h_min=h_min,
        content_hash=h,
        elapsed_s=0.0,
    )


def _signed_volumes(xyz: np.ndarray, tets: np.ndarray) -> np.ndarray:
    a, b, c, d = (xyz[tets[:, i]] for i in range(4))
    return np.asarray(np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a) / 6.0)
