# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``EMProblem`` to :class:`oa_fdtd.model.Model`: mesh, dispersive dielectrics, copper, ports.

This is the geometry front end of ADR-0010 (the adapter of WP-507 calls :func:`build_model`).

- **Mesh** (``oa_mesh.rect.smooth_axis``): the largest cell is ``lambda_min / N`` in the densest
  dielectric (N = 10/20/30 for coarse/medium/fine, or ``mesh.cells_per_wavelength``); fixed lines
  on every conductor edge, via and port coordinate, on dielectric interfaces (4 cells across each
  layer) and on both copper faces, which bound exactly one cell; grading from ``mesh.max_grading``.
  Absorbing faces get ``pml_cells`` cells of the boundary cell size outside the problem box.
- **Dielectrics:** each model is evaluated by ``oa_physics`` and fitted by a multi-pole Debye sum
  (:mod:`oa_fdtd.materials`) over ``max(f_min, f_max / 100) .. f_max`` for the ADE kernels.
- **Copper:** polygons are rasterised on the mesh (cell centres) and merged into boxes, each a
  :class:`~oa_fdtd.model.Conductor` with the finite-slab surface impedance and the roughness of
  the body; vias are solid PEC square barrels of the area of the round via
  (:func:`_via_side`, the sub-cell via model). Planes and slabs that touch an absorbing face
  over at least half of it continue through the PML; traces end at the problem box.
- **Ports:** ``EMLumpedPort`` becomes a :class:`~oa_fdtd.model.LumpedPort` on the terminal
  column; ``EMWavePort`` a quasi-TEM :class:`~oa_fdtd.model.ModePort` on the boundary face (the
  face becomes a PMC wall behind the port sheet, like a lumped port).

Limits: staircased lateral conductor edges (the via barrels are exact on the mesh lines, as equal
area squares; no Dey-Mittra cells), diagonal
polygons are rasterised at cell centres, anisotropy and lumped RLC elements are ignored with a
diagnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from oa_fdtd.grid import Grid
from oa_fdtd.materials import fit_callable
from oa_fdtd.model import Conductor, LumpedPort, Model, ModelError, ModePort, Region, Waveform
from oa_mesh.rect import max_cell_size, smooth_axis
from oa_model.em import ConductorBody, EMLumpedPort, EMProblem, EMWavePort
from oa_model.materials import ConductorMaterial, DielectricMaterial
from oa_physics.conductor import roughness_factor
from oa_physics.dielectric import as_causal, eps_complex

FloatArray = npt.NDArray[np.float64]
_FACES = ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")
_PRESET_N = {"coarse": 10.0, "medium": 20.0, "fine": 30.0}
_BIG = 1.0  # [m] boxes reach this far past the grid so that no face edge lies on a wall


@dataclass
class BuiltModel:
    """The engine model and what was decided on the way."""

    model: Model
    meta: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)


def frequency_grid(problem: EMProblem) -> FloatArray:
    """Analysis frequencies [Hz] (the rule of ``oa_em_openems.scene.frequency_grid``)."""
    fp = problem.frequency
    n = fp.n_points
    f_lo = fp.f_min if fp.f_min > 0 else fp.f_max / max(n - 1, 1)
    f_lo = min(f_lo, fp.f_max * (1 - 1e-6))
    grid = (
        np.geomspace(f_lo, fp.f_max, n) if fp.spacing == "log" else np.linspace(f_lo, fp.f_max, n)
    )
    extra = np.array(fp.extra_points, dtype=np.float64)
    return np.unique(np.concatenate([grid, extra]))


def _inside(ring: FloatArray, px: FloatArray, py: FloatArray) -> npt.NDArray[np.bool_]:
    """Even-odd point-in-ring test for flat arrays of points."""
    x0, y0 = ring[:, 0], ring[:, 1]
    x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
    inside = np.zeros(px.shape, dtype=bool)
    for a, b, c, d in zip(x0, y0, x1, y1, strict=True):
        if b == d:
            continue
        cond = (b > py) != (d > py)
        xint = a + (py - b) * (c - a) / (d - b)
        inside ^= cond & (px < xint)
    return inside


def _poly_mask(poly: Any, xc: FloatArray, yc: FloatArray) -> npt.NDArray[np.bool_]:
    px, py = np.meshgrid(xc, yc, indexing="ij")
    ext = _inside(np.asarray(poly.exterior.array), px.ravel(), py.ravel())
    for hole in poly.holes:
        ext &= ~_inside(np.asarray(hole.array), px.ravel(), py.ravel())
    return ext.reshape(px.shape)


def _rects(mask: npt.NDArray[np.bool_]) -> list[tuple[int, int, int, int]]:
    """Cover a boolean ``(nx, ny)`` mask by rectangles ``(i0, i1, j0, j1)`` (half open)."""
    nx = mask.shape[0]
    out: list[tuple[int, int, int, int]] = []
    open_runs: dict[tuple[int, int], int] = {}
    for i in range(nx + 1):
        runs: set[tuple[int, int]] = set()
        if i < nx:
            col = np.concatenate([[False], mask[i], [False]])
            edges = np.flatnonzero(col[1:] != col[:-1])
            runs = {(int(a), int(b)) for a, b in zip(edges[0::2], edges[1::2], strict=True)}
        for run in list(open_runs):
            if run not in runs:
                out.append((open_runs.pop(run), i, run[0], run[1]))
        for run in runs:
            open_runs.setdefault(run, i)
    return out


def _bbox2(body: ConductorBody) -> tuple[float, float, float, float]:
    pts = np.concatenate([np.asarray(p.exterior.array) for p in body.polygons])
    return (
        float(pts[:, 0].min()),
        float(pts[:, 1].min()),
        float(pts[:, 0].max()),
        float(pts[:, 1].max()),
    )


def _via_side(r_outer: float) -> float:
    """Side [m] of the square PEC barrel that replaces a round via of radius ``r_outer``.

    Equal cross-section area (``a = sqrt(pi) r``): the geometric-mean-distance radius of a square
    is within 2 % of the round conductor's (0.792 r against 0.779 r), so the external inductance
    and the capacitance to the antipad edge match to first order, and the barrel lies exactly on
    mesh lines (the mesh places lines at the square's faces). A staircased circle at cell centres
    changes its area, and with it the via inductance, with every mesh refinement.
    """
    return float(np.sqrt(np.pi) * r_outer)


def build_model(
    problem: EMProblem,
    *,
    copper: str = "thick_sibc",
    pml_cells: int = 10,
    bandwidth: float = 1.4,
    pulse: str = "gauss_mod",
) -> BuiltModel:
    """Translate ``problem`` into an engine model; raises :class:`ModelError` on bad input."""
    st = problem.structure
    box = st.bbox
    diag: list[str] = []
    freqs = frequency_grid(problem)
    f_max = float(freqs[-1])
    f_fit_lo = max(float(freqs[0]), f_max / 100.0)

    # ---- materials
    diel: dict[str, DielectricMaterial] = {}
    sigma_cu: dict[str, float] = {}
    for m in st.materials:
        if isinstance(m, DielectricMaterial):
            diel[m.name] = m
            if abs(m.dk_z_over_dk_xy - 1.0) > 1e-9:
                diag.append(f"FDTD_ANISOTROPY_IGNORED: {m.name} dk_z/dk_xy")
        elif isinstance(m, ConductorMaterial):
            sigma_cu[m.name] = m.conductivity
    fits: dict[str, Any] = {}
    eps_hi = 1.0
    for name, m in diel.items():
        model_c = as_causal(m.model)
        fit = fit_callable(lambda f, mc=model_c: eps_complex(mc, f), f_fit_lo, f_max)  # type: ignore[misc]
        fits[name] = fit
        eps_hi = max(eps_hi, float(fit.eval(np.array([f_max]))[0].real), fit.eps_inf)
    if st.background in diel:
        diag.append("FDTD_BACKGROUND_IGNORED: only vacuum outside the slabs")
    if st.lumped:
        diag.append("FDTD_LUMPED_ELEMENTS_IGNORED: SMD R/L/C placeholders are not modelled")

    n_cpw = problem.mesh.cells_per_wavelength or _PRESET_N.get(problem.mesh.preset, 20.0)
    d_max = max_cell_size(f_max, eps_hi, round(n_cpw))
    grading = problem.mesh.max_grading
    if problem.mesh.min_cell:
        d_max = max(d_max, problem.mesh.min_cell)

    # ---- boundaries
    bcs = {
        f: getattr(problem.boundaries, f) for f in ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")
    }
    wave_faces = {p.face for p in problem.ports if isinstance(p, EMWavePort)}
    wall: dict[str, str] = {}
    for f, bc in bcs.items():
        wall[f] = "pmc" if f in wave_faces else ("cpml" if bc in ("absorbing", "pml") else bc)
    has_pml = {f: wall[f] == "cpml" for f in _FACES}

    # ---- mesh seeds
    lo3 = (box.xmin, box.ymin, box.zmin)
    hi3 = (box.xmax, box.ymax, box.zmax)
    seeds: list[list[tuple[float, float]]] = [[(lo3[a], d_max), (hi3[a], d_max)] for a in range(3)]
    locked: list[float] = []
    thin: list[tuple[ConductorBody, float]] = []
    for body in st.conductors:
        x0, y0, x1, y1 = _bbox2(body)
        w = min(x1 - x0, y1 - y0)
        s_edge = min(d_max, w / 4.0) if w < 8.0 * d_max else d_max
        for p in body.polygons:
            ring = np.asarray(p.exterior.array)
            for a in (0, 1):
                for v in np.unique(np.round(ring[:, a], 9)):
                    if lo3[a] - 1e-9 <= v <= hi3[a] + 1e-9:
                        seeds[a].append((float(v), s_edge))
        t = body.z_top - body.z_bottom
        seeds[2] += [(body.z_bottom, min(t, d_max)), (body.z_top, min(t, d_max))]
        locked += [body.z_bottom, body.z_top]
        thin.append((body, t))
    for v in st.vias:
        half = 0.5 * _via_side(v.r_outer)
        s = min(d_max, half)
        for a, c in ((0, v.center[0]), (1, v.center[1])):
            seeds[a] += [(c - half, s), (c, s), (c + half, s)]
        seeds[2] += [(v.z_bottom, d_max), (v.z_top, d_max)]
    for slab in st.slabs:
        t = slab.z_top - slab.z_bottom
        size = min(d_max, t / 4.0)
        seeds[2] += [
            (slab.z_bottom, size),
            (slab.z_top, size),
            (0.5 * (slab.z_bottom + slab.z_top), size),
        ]
    for port in problem.ports:
        if isinstance(port, EMWavePort):
            a_u = 1 if port.face.startswith("x") else 0
            seeds[a_u] += [(port.rect[0], d_max), (port.rect[1], d_max)]
            seeds[2] += [(port.rect[2], d_max), (port.rect[3], d_max)]
        elif isinstance(port, EMLumpedPort):
            for term in (port.positive, port.negative):
                seeds[2].append((term.z, min(d_max, port.width / 2.0)))
            x, y = port.positive.position
            seeds[0] += [(x, min(d_max, port.width / 2.0))]
            seeds[1] += [(y - port.width / 2.0, min(d_max, port.width / 2.0)),
                         (y + port.width / 2.0, min(d_max, port.width / 2.0))]  # fmt: skip
    lines: list[FloatArray] = []
    for a in range(3):
        seeds_a = [s for s in seeds[a] if lo3[a] - 1e-9 <= s[0] <= hi3[a] + 1e-9]
        mesh = smooth_axis(seeds_a, d_max, grading, locked if a == 2 else ())
        ln = np.asarray(mesh.lines, dtype=np.float64)
        n_lo = pml_cells if has_pml[_FACES[2 * a]] else 0
        n_hi = pml_cells if has_pml[_FACES[2 * a + 1]] else 0
        if n_lo:
            ln = np.concatenate([ln[0] - (ln[1] - ln[0]) * np.arange(n_lo, 0, -1), ln])
        if n_hi:
            ln = np.concatenate([ln, ln[-1] + (ln[-1] - ln[-2]) * np.arange(1, n_hi + 1)])
        lines.append(ln)
    grid = Grid(*lines)
    cx, cy = grid.centers(0), grid.centers(1)

    # ---- dielectric slabs
    regions: list[Region] = []
    tol = 1e-9
    for slab in st.slabs:
        fit = fits[slab.material]
        z_lo = grid.z[0] - _BIG if slab.z_bottom <= box.zmin + tol else slab.z_bottom
        z_hi = grid.z[-1] + _BIG if slab.z_top >= box.zmax - tol else slab.z_top
        if slab.footprint is None:
            x_lo, x_hi = grid.x[0] - _BIG, grid.x[-1] + _BIG
            y_lo, y_hi = grid.y[0] - _BIG, grid.y[-1] + _BIG
            regions.append(
                Region((x_lo, y_lo, z_lo), (x_hi, y_hi, z_hi), fit.eps_inf, 0.0, False, fit.poles)
            )
        else:
            mask = _poly_mask(slab.footprint, cx, cy)
            for i0, i1, j0, j1 in _rects(mask):
                lo = (
                    grid.x[i0] - (_BIG if i0 == 0 else 0),
                    grid.y[j0] - (_BIG if j0 == 0 else 0),
                    z_lo,
                )
                hi = (
                    grid.x[i1] + (_BIG if i1 == cx.size else 0),
                    grid.y[j1] + (_BIG if j1 == cy.size else 0),
                    z_hi,
                )
                regions.append(Region(lo, hi, fit.eps_inf, 0.0, False, fit.poles))

    # ---- copper
    conductors: list[Conductor] = []
    n_box = 0
    for body, t in thin:
        sigma = sigma_cu.get(body.material)
        if sigma is None:
            raise ModelError("FDTD_NO_MATERIAL", f"conductor material {body.material!r} missing")
        bx0, by0, bx1, by1 = _bbox2(body)
        face_ext = {
            "xmin": bx0 <= box.xmin + tol and (by1 - by0) >= 0.5 * (box.ymax - box.ymin),
            "xmax": bx1 >= box.xmax - tol and (by1 - by0) >= 0.5 * (box.ymax - box.ymin),
            "ymin": by0 <= box.ymin + tol and (bx1 - bx0) >= 0.5 * (box.xmax - box.xmin),
            "ymax": by1 >= box.ymax - tol and (bx1 - bx0) >= 0.5 * (box.xmax - box.xmin),
        }
        xs = np.where(cx < box.xmin, box.xmin + 1e-9 if face_ext["xmin"] else cx, cx)
        xs = np.where(xs > box.xmax, box.xmax - 1e-9 if face_ext["xmax"] else xs, xs)
        ys = np.where(cy < box.ymin, box.ymin + 1e-9 if face_ext["ymin"] else cy, cy)
        ys = np.where(ys > box.ymax, box.ymax - 1e-9 if face_ext["ymax"] else ys, ys)
        mask = np.zeros((cx.size, cy.size), dtype=bool)
        for poly in body.polygons:
            mask |= _poly_mask(poly, xs, ys)
        k_func = None
        if copper == "thick_sibc" and body.roughness.kind != "none":
            k_func = lambda f, r=body.roughness, s=sigma: roughness_factor(r, f, s)  # noqa: E731
        z_lo = grid.z[0] - _BIG if body.z_bottom <= grid.z[0] + tol else body.z_bottom
        z_hi = grid.z[-1] + _BIG if body.z_top >= grid.z[-1] - tol else body.z_top
        for i0, i1, j0, j1 in _rects(mask):
            lo = (
                grid.x[i0] - (_BIG if i0 == 0 else 0.0),
                grid.y[j0] - (_BIG if j0 == 0 else 0.0),
                z_lo,
            )
            hi = (
                grid.x[i1] + (_BIG if i1 == cx.size else 0.0),
                grid.y[j1] + (_BIG if j1 == cy.size else 0.0),
                z_hi,
            )
            conductors.append(
                Conductor(lo, hi, sigma, t, 0.0, k_func, "pec" if copper == "pec" else "thick_sibc")
            )
            n_box += 1
    for v in st.vias:
        px, py = np.meshgrid(cx, cy, indexing="ij")
        half = 0.5 * _via_side(v.r_outer)
        mask = (np.abs(px - v.center[0]) <= half) & (np.abs(py - v.center[1]) <= half)
        if not mask.any():
            diag.append(f"FDTD_VIA_UNRESOLVED: {v.id} smaller than a cell")
            continue
        for i0, i1, j0, j1 in _rects(mask):
            conductors.append(
                Conductor(
                    (grid.x[i0], grid.y[j0], v.z_bottom),
                    (grid.x[i1], grid.y[j1], v.z_top),
                    5.8e7,
                    None,
                    0.0,
                    None,
                    "pec",
                )
            )

    # ---- ports
    by_id = {b.id: b for b in st.conductors}
    ports: list[LumpedPort | ModePort] = []
    for port in problem.ports:
        if isinstance(port, EMLumpedPort):
            x, y = port.positive.position
            z_p, z_n = port.positive.z, port.negative.z
            ports.append(
                LumpedPort(
                    port.name,
                    2,
                    (x, y - 0.5 * port.width, min(z_p, z_n)),
                    (x, y + 0.5 * port.width, max(z_p, z_n)),
                    port.z0,
                    1.0 if z_p >= z_n else -1.0,
                )
            )
        elif isinstance(port, EMWavePort):
            n = 0 if port.face.startswith("x") else 1
            pos = getattr(box, port.face)
            sigs = []
            for sid in port.signal_conductors:
                body = by_id[sid]
                bx0, by0, bx1, by1 = _bbox2(body)
                u0, u1 = (by0, by1) if n == 0 else (bx0, bx1)
                sigs.append(((u0, body.z_bottom), (u1, body.z_top)))
            for mode in range(port.n_modes):
                name = port.name if port.n_modes == 1 else f"{port.name}:m{mode}"
                ports.append(
                    ModePort(
                        name,
                        n,
                        pos,
                        (port.rect[0], port.rect[2]),
                        (port.rect[1], port.rect[3]),
                        tuple(sigs),
                        mode,
                        1 if port.face.endswith("min") else -1,
                    )
                )
        else:
            raise ModelError("FDTD_PORT_KIND", f"port kind {port.kind!r} is not supported")

    walls = (wall["xmin"], wall["xmax"], wall["ymin"], wall["ymax"], wall["zmin"], wall["zmax"])
    # "gauss_mod": modulated Gaussian, no energy at DC (its spectrum falls linearly to zero, so
    # the three-plane wave extraction is noise-limited below ~0.05 f_max on lossless lines);
    # "gauss": Gaussian with DC content (good low-frequency S, but the energy end criterion
    # takes about 10x more steps on open structures).
    wf = (
        Waveform("gauss", 0.0, bandwidth * f_max)
        if pulse == "gauss"
        else Waveform("gauss_mod", 0.5 * f_max, bandwidth * f_max)
    )
    model = Model(
        grid,
        wf,
        freqs,
        regions=regions,
        conductors=conductors,
        walls=walls,
        ports=ports,
    )
    meta = {
        "cells": grid.cells,
        "shape": grid.n,
        "d_max": d_max,
        "min_cell": min(float(np.min(grid.d(a))) for a in range(3)),
        "debye_poles": {k: len(v.delta) for k, v in fits.items()},
        "debye_err": {k: (v.err_eps, v.err_tand) for k, v in fits.items()},
        "conductor_boxes": n_box,
    }
    return BuiltModel(model, meta, diag)
