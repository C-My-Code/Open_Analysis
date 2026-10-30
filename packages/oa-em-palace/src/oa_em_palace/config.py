# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``EMProblem`` + WP-206 group sidecar -> Palace ``config.json`` (Palace 0.18, JSON schema v0.18).

Pure functions: no gmsh, no Palace, no file system. The mesh is addressed by the physical-group
tags of ``<mesh>.groups.json`` (``oa_mesh.occ3d``); Palace reads the same numbers as attributes.

Units: the WP-206 mesh is written in metres, so ``Model.L0 = 1`` and every length in the file
(``Offset``, ``Thickness``) is in metres. Frequencies in Palace's driven sweep are in GHz.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from oa_model.common import Diagnostic
from oa_model.em import (
    ConductorBody,
    EMCoaxPort,
    EMLumpedPort,
    EMProblem,
    EMTerminalGeom,
    EMWavePort,
    FullWaveDriven,
)
from oa_model.materials import ConductorMaterial, DielectricMaterial, NoRoughness
from oa_physics import eps_complex

__all__ = [
    "Band",
    "PalaceOptions",
    "build_config",
    "dielectric_fit_frequency",
    "plan_bands",
    "sweep_frequencies",
]

_GHZ = 1e9
_AXES = {
    (1.0, 0.0, 0.0): "+X",
    (-1.0, 0.0, 0.0): "-X",
    (0.0, 1.0, 0.0): "+Y",
    (0.0, -1.0, 0.0): "-Y",
    (0.0, 0.0, 1.0): "+Z",
    (0.0, 0.0, -1.0): "-Z",
}
_COPPER = ConductorMaterial(name="copper", conductivity=5.8e7)


@dataclass(frozen=True)
class PalaceOptions:
    """Palace expert keys (``MeshSettings.expert``, namespace ``palace.``) with their defaults.

    ``conductor_model``: ``"sheet"`` (default) puts a finite-conductivity surface boundary (with
    the real thickness for sheets) on every conductor surface; ``"pec"`` makes them perfect.
    ``abc_order``: order of the absorbing boundary (1 or 2). ``eps_freq``: frequency [Hz] at which
    the dielectrics are evaluated (default: ``f_max / 2``; Palace has no frequency-dependent
    materials). ``linear_tol``: GMRES tolerance. ``solver``: ``"AMS"`` (default, about 3.5 GB for
    230k tets) or ``"SuperLU"``/``"Default"`` (sparse direct: far more memory).
    ``adaptive_tol``: fast-sweep ROM tolerance when ``frequency.adaptive`` is set. ``device``:
    ``"CPU"`` or ``"GPU"`` (GPU hook; needs a CUDA/HIP build). ``f_min_fraction``: lowest sweep
    point as a fraction of ``f_max`` when ``frequency.f_min == 0``. ``banded``: split the sweep
    into bands (``plan_bands``), one Palace run each, dielectrics at the band centre, so the
    change of eps' is at most ``eps_tol`` and of tan d at most ``tand_tol`` inside a band.
    """

    conductor_model: str = "sheet"
    abc_order: int = 1
    eps_freq: float | None = None
    linear_tol: float = 1e-6
    solver: str = "AMS"
    adaptive_tol: float = 1e-3
    device: str = "CPU"
    f_min_fraction: float = 0.1
    amr_update_fraction: float = 0.7
    banded: bool = False
    eps_tol: float = 0.002
    tand_tol: float = 0.02

    @classmethod
    def from_expert(cls, expert: Mapping[str, Any]) -> PalaceOptions:
        base = cls()
        kwargs: dict[str, Any] = {}
        for name in base.__dataclass_fields__:
            key = f"palace.{name}"
            if key in expert:
                kwargs[name] = expert[key]
        return cls(**{**base.__dict__, **kwargs})


@dataclass(frozen=True)
class Band:
    """One sub-band of a banded sweep: Palace 0.18 has no dispersive materials, so each band is a
    separate run whose dielectrics are evaluated at ``f_fit`` [Hz] (the band centre)."""

    f_lo: float  # [Hz]
    f_hi: float  # [Hz]
    f_fit: float  # [Hz]
    freqs: tuple[float, ...]  # sweep points of the band [Hz]


def sweep_frequencies(problem: EMProblem, options: PalaceOptions) -> np.ndarray:
    """The sweep points [Hz] the translation asks Palace for, ascending (see ``_sample_blocks``)."""
    fp = problem.frequency
    f_hi = fp.f_max
    f_lo = fp.f_min if fp.f_min > 0 else options.f_min_fraction * f_hi
    if fp.spacing == "log":
        pts = np.geomspace(f_lo, f_hi, fp.n_points)
    else:
        pts = np.linspace(f_lo, f_hi, fp.n_points)
    return np.unique(np.concatenate([pts, np.asarray(fp.extra_points, dtype=np.float64)]))


def _band_deviation(problem: EMProblem, f_fit: float, f_a: float, f_b: float) -> tuple[float, float]:
    """Largest relative deviation of eps' and tan d over [f_a, f_b] from their value at f_fit."""
    d_er = d_td = 0.0
    for m in problem.structure.materials:
        if not isinstance(m, DielectricMaterial):
            continue
        e = np.asarray(eps_complex(m.model, np.array([f_fit, f_a, f_b])))
        er = e.real
        td = -e.imag / e.real
        d_er = max(d_er, float(np.max(np.abs(er[1:] - er[0]) / er[0])))
        if td[0] > 1e-12:
            d_td = max(d_td, float(np.max(np.abs(td[1:] - td[0]) / td[0])))
    return d_er, d_td


def plan_bands(problem: EMProblem, options: PalaceOptions) -> list[Band]:
    """Split the sweep into bands inside which the dielectric changes at most ``eps_tol`` (eps')
    and ``tand_tol`` (tan d) relative to its value at the band centre (greedy, ascending)."""
    f = sweep_frequencies(problem, options)
    bands: list[Band] = []
    i = 0
    while i < f.size:
        j = i
        while j + 1 < f.size:
            fit = 0.5 * (f[i] + f[j + 1])
            d_er, d_td = _band_deviation(problem, fit, float(f[i]), float(f[j + 1]))
            if d_er > options.eps_tol or d_td > options.tand_tol:
                break
            j += 1
        fit = 0.5 * (f[i] + f[j])
        bands.append(Band(float(f[i]), float(f[j]), float(fit), tuple(float(x) for x in f[i : j + 1])))
        i = j + 1
    return bands


@dataclass
class ConfigInfo:
    """What the translation decided, for the run sidecar and the tests."""

    eps_freq: float
    port_index: dict[str, int] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    dielectric_fit: dict[str, dict[str, float]] = field(default_factory=dict)
    sweep_ghz: tuple[float, float, int] = (0.0, 0.0, 0)
    band_info: list[dict[str, Any]] = field(default_factory=list)


def dielectric_fit_frequency(problem: EMProblem, options: PalaceOptions) -> float:
    """Frequency [Hz] at which dielectrics are evaluated (band centre unless overridden)."""
    if options.eps_freq is not None:
        return float(options.eps_freq)
    return 0.5 * problem.frequency.f_max


def _diag(severity: str, code: str, message: str, hint: str | None = None) -> Diagnostic:
    return Diagnostic(severity=severity, code=code, message=message, hint=hint)  # type: ignore[arg-type]


def _direction(pos: EMTerminalGeom, neg: EMTerminalGeom) -> str | list[float]:
    """Field direction of a lumped port: from the positive towards the negative terminal."""
    v = np.array(
        [neg.position[0] - pos.position[0], neg.position[1] - pos.position[1], neg.z - pos.z]
    )
    n = float(np.linalg.norm(v))
    if n == 0.0:
        raise ValueError("lumped port terminals coincide")
    u = v / n
    for axis, name in _AXES.items():
        if float(np.linalg.norm(u - np.array(axis))) < 1e-3:
            return name
    return [round(float(c), 9) for c in u]


def _eps_table(
    problem: EMProblem, f_fit: float, info: ConfigInfo
) -> dict[str, tuple[float, float, float]]:
    """name -> (eps_r', tan_delta, dk_z/dk_xy) at ``f_fit``; records the band variation."""
    out: dict[str, tuple[float, float, float]] = {}
    f_hi = problem.frequency.f_max
    f_lo = max(problem.frequency.f_min, 0.1 * f_hi)
    for m in problem.structure.materials:
        if not isinstance(m, DielectricMaterial):
            continue
        e = complex(eps_complex(m.model, [f_fit])[0])
        er = float(e.real)
        tand = float(-e.imag / e.real) if e.real else 0.0
        out[m.name] = (er, max(tand, 0.0), float(m.dk_z_over_dk_xy))
        band = np.asarray(eps_complex(m.model, np.array([f_lo, f_hi])))
        d_er = float(abs(band[1].real - band[0].real) / er) if er else 0.0
        tands = -band.imag / band.real
        d_td = float(abs(tands[1] - tands[0]) / tand) if tand > 0 else 0.0
        info.dielectric_fit[m.name] = {
            "f_fit_hz": f_fit,
            "eps_r": er,
            "tan_delta": tand,
            "eps_variation": d_er,
            "tan_delta_variation": d_td,
        }
        if d_er > 0.02 or d_td > 0.10:
            info.diagnostics.append(
                _diag(
                    "warning",
                    "PALACE.DIELECTRIC_FIT",
                    f"{m.name}: Palace has no frequency-dependent materials; eps_r is fixed at "
                    f"{f_fit / 1e9:.3g} GHz and varies {100 * d_er:.1f} % (eps) and "
                    f"{100 * d_td:.0f} % (tan d) over {f_lo / 1e9:.3g}-{f_hi / 1e9:.3g} GHz",
                    "Split the band into sub-bands (palace.eps_freq) when dispersion matters.",
                )
            )
    return out


def _is_external(body: ConductorBody | None, problem: EMProblem) -> bool:
    """A conductor sheet lies on the exterior of the domain when it is a full-size plane at z min
    or z max of the region (reference planes of microstrip and stripline)."""
    if body is None:
        return False
    box = problem.structure.bbox
    tol = 1e-9
    zs = (body.z_bottom, body.z_top)
    on_z_face = min(abs(z - box.zmin) for z in zs) < tol or min(abs(z - box.zmax) for z in zs) < tol
    on_z_face = on_z_face or any(
        abs(s.z_bottom - body.z_top) < tol and abs(s.z_bottom - box.zmin) < tol
        for s in problem.structure.slabs
    )
    if not on_z_face:
        return False
    for poly in body.polygons:
        ext = np.asarray(poly.exterior.array)
        x_lo, x_hi = float(np.min(ext[:, 0])), float(np.max(ext[:, 0]))
        y_lo, y_hi = float(np.min(ext[:, 1])), float(np.max(ext[:, 1]))
        covers_x = x_lo <= box.xmin + tol and x_hi >= box.xmax - tol
        if covers_x and y_lo <= box.ymin + tol and y_hi >= box.ymax - tol:
            return True
    return False


def _sample_blocks(problem: EMProblem, options: PalaceOptions, info: ConfigInfo) -> list[Any]:
    fp = problem.frequency
    f_hi = fp.f_max
    f_lo = fp.f_min if fp.f_min > 0 else options.f_min_fraction * f_hi
    if fp.f_min == 0:
        info.diagnostics.append(
            _diag(
                "info",
                "PALACE.NO_DC",
                f"Palace cannot solve at 0 Hz; the sweep starts at {f_lo / _GHZ:.4g} GHz",
                "The quality gate treats a missing DC point per its own settings.",
            )
        )
    info.sweep_ghz = (f_lo / _GHZ, f_hi / _GHZ, fp.n_points)
    block: dict[str, Any] = {
        "Type": "Log" if fp.spacing == "log" else "Linear",
        "MinFreq": round(f_lo / _GHZ, 12),
        "MaxFreq": round(f_hi / _GHZ, 12),
        "NSample": fp.n_points,
    }
    blocks: list[Any] = [block]
    if fp.extra_points:
        blocks.append({"Type": "Point", "Freq": [round(f / _GHZ, 12) for f in fp.extra_points]})
    return blocks


def build_config(
    problem: EMProblem,
    groups: Mapping[str, Any],
    *,
    mesh_file: str,
    output_dir: str = "postpro",
    options: PalaceOptions | None = None,
    band: Band | None = None,
) -> tuple[dict[str, Any], ConfigInfo]:
    """The Palace ``config.json`` of ``problem`` on the mesh described by ``groups``.

    ``groups`` is the parsed ``<mesh>.groups.json`` sidecar. Raises ``ValueError`` for a problem
    the translation cannot express (``PalaceAdapter.check`` reports these first).
    """
    opts = options or PalaceOptions.from_expert(problem.mesh.expert)
    if not isinstance(problem.physics, FullWaveDriven):
        raise ValueError(
            f"Palace adapter supports fullwave_driven only, got {problem.physics.kind}"
        )
    f_fit = band.f_fit if band is not None else dielectric_fit_frequency(problem, opts)
    info = ConfigInfo(eps_freq=f_fit)
    eps = _eps_table(problem, f_fit, info)
    infos = list(groups["groups"])
    bodies = {b.id: b for b in problem.structure.conductors}
    cmat = {m.name: m for m in problem.structure.materials if isinstance(m, ConductorMaterial)}

    # ---- domains -------------------------------------------------------------------------
    materials: list[dict[str, Any]] = []
    for g in infos:
        if g["kind"] != "material":
            continue
        er, tand, ratio = eps.get(g["model_id"], (1.0, 0.0, 1.0))
        entry: dict[str, Any] = {"Attributes": [g["tag"]]}
        if ratio != 1.0:
            entry["Permittivity"] = [er, er, round(er * ratio, 9)]
            entry["LossTan"] = [tand, tand, tand]
        else:
            entry["Permittivity"] = round(er, 9)
            entry["LossTan"] = round(tand, 9)
        entry["Permeability"] = 1.0
        materials.append(entry)

    # ---- boundaries ----------------------------------------------------------------------
    bc_of = dict(groups.get("boundary_bc", {}))
    pec: list[int] = []
    pmc: list[int] = []
    absorbing: list[int] = []
    for g in infos:
        if g["kind"] != "boundary":
            continue
        bc = bc_of.get(g["model_id"], "absorbing")
        if bc == "pec":
            pec.append(g["tag"])
        elif bc == "pmc":
            pmc.append(g["tag"])
        else:
            absorbing.append(g["tag"])
            if bc == "pml":
                info.diagnostics.append(
                    _diag(
                        "warning",
                        "PALACE.PML_AS_ABC",
                        f"face {g['model_id']}: Palace 0.18 has no PML; a "
                        f"{opts.abc_order}-order absorbing boundary is used",
                        "Increase air_padding to lower the reflection of the boundary.",
                    )
                )

    conductivity: list[dict[str, Any]] = []
    conductor_tags: list[int] = []
    rough: list[str] = []
    for g in infos:
        if g["kind"] not in ("conductor", "via"):
            continue
        conductor_tags.append(g["tag"])
        body = bodies.get(g["model_id"])
        if body is not None and not isinstance(body.roughness, NoRoughness):
            rough.append(body.id)
        if opts.conductor_model == "pec":
            pec.append(g["tag"])
            continue
        mat = cmat.get(body.material, _COPPER) if body is not None else _COPPER
        entry = {"Attributes": [g["tag"]], "Conductivity": float(mat.conductivity)}
        if mat.mu_r != 1.0:
            entry["Permeability"] = float(mat.mu_r)
        if g.get("representation") == "sheet" and body is not None:
            t = body.z_top - body.z_bottom
            if t > 0:
                entry["Thickness"] = round(t, 12)
        elif g["kind"] == "via":
            via = next((v for v in problem.structure.vias if v.id == g["model_id"]), None)
            if via is not None and 0 < via.plating < via.r_outer:
                entry["Thickness"] = round(via.plating, 12)
        if _is_external(body, problem):
            entry["External"] = True
        conductivity.append(entry)
    if rough:
        info.diagnostics.append(
            _diag(
                "warning",
                "PALACE.ROUGHNESS_IGNORED",
                "surface roughness is not modelled by the Palace adapter: " + ", ".join(rough),
                "Use a rough-copper conductivity or compare against openEMS/closed forms.",
            )
        )

    lumped: list[dict[str, Any]] = []
    wave: list[dict[str, Any]] = []
    # ``"Excitation": true`` is excitation group 1 for every port (all driven at once); one
    # group per excited port gives one S-matrix column each (verified against Palace 0.18.1)
    group: dict[str, int] = {}
    for port in problem.ports:
        if port.excite:
            group[port.name] = len(group) + 1
    port_group = {p["name"]: p["group"] for p in groups.get("ports", [])}
    tag_of = {g["name"]: g["tag"] for g in infos}
    for idx, port in enumerate(problem.ports, start=1):
        info.port_index[port.name] = idx
        tag = tag_of.get(port_group.get(port.name, f"port:{port.name}"))
        if tag is None:
            raise ValueError(f"port {port.name!r} has no physical group in the mesh sidecar")
        if isinstance(port, EMLumpedPort):
            lumped.append(
                {
                    "Index": idx,
                    "Attributes": [tag],
                    "Direction": _direction(port.positive, port.negative),
                    "R": float(port.z0),
                    "Excitation": group.get(port.name, False),
                }
            )
        elif isinstance(port, EMWavePort):
            entry = {
                "Index": idx,
                "Attributes": [tag],
                "Mode": 1,
                "Offset": round(float(port.deembed), 12),
                "Excitation": group.get(port.name, False),
            }
            if port.n_modes != 1:
                info.diagnostics.append(
                    _diag(
                        "warning",
                        "PALACE.MULTIMODE",
                        f"port {port.name}: only mode 1 is used (n_modes={port.n_modes})",
                    )
                )
            wave.append(entry)
        elif isinstance(port, EMCoaxPort):
            lumped.append(
                {
                    "Index": idx,
                    "Attributes": [tag],
                    "Direction": "+R" if port.direction == "+z" else "-R",
                    "CoordinateSystem": "Cylindrical",
                    "R": float(port.z0),
                    "Excitation": group.get(port.name, False),
                }
            )

    boundaries: dict[str, Any] = {}
    if pec:
        boundaries["PEC"] = {"Attributes": sorted(pec)}
    if pmc:
        boundaries["PMC"] = {"Attributes": sorted(pmc)}
    if absorbing:
        boundaries["Absorbing"] = {"Attributes": sorted(absorbing), "Order": int(opts.abc_order)}
    if conductivity:
        boundaries["Conductivity"] = conductivity
    if lumped:
        boundaries["LumpedPort"] = lumped
    if wave:
        boundaries["WavePort"] = wave
        if conductivity:
            # the 2D mode solve treats the signal strips as perfect conductors
            boundaries["WavePortPEC"] = {"Attributes": sorted(conductor_tags)}

    # ---- solver --------------------------------------------------------------------------
    if band is not None:
        samples: list[Any] = [{"Type": "Point", "Freq": [round(x / _GHZ, 12) for x in band.freqs]}]
        info.sweep_ghz = (band.f_lo / _GHZ, band.f_hi / _GHZ, len(band.freqs))
    else:
        samples = _sample_blocks(problem, opts, info)
    driven: dict[str, Any] = {"Samples": samples}
    if problem.frequency.adaptive:
        driven["AdaptiveTol"] = opts.adaptive_tol
    linear: dict[str, Any] = {
        "Type": opts.solver,
        "KSPType": "GMRES",
        "Tol": opts.linear_tol,
        "MaxIts": 200,
    }
    solver = {
        "Order": int(problem.mesh.fem_order),
        "Device": opts.device,
        "Driven": driven,
        "Linear": linear,
    }
    model: dict[str, Any] = {"Mesh": mesh_file, "L0": float(groups.get("length_unit_m", 1.0))}
    if problem.mesh.amr_max_passes > 0:
        model["Refinement"] = {
            "MaxIts": int(problem.mesh.amr_max_passes),
            "Tol": float(problem.convergence.delta_s),
            "UpdateFraction": opts.amr_update_fraction,
            "Nonconformal": True,
            "SaveAdaptIterations": False,
        }
    config = {
        "Problem": {"Type": "Driven", "Verbose": 2, "Output": output_dir},
        "Model": model,
        "Domains": {"Materials": materials},
        "Boundaries": boundaries,
        "Solver": solver,
    }
    if not math.isfinite(problem.frequency.f_max):
        raise ValueError("frequency.f_max must be finite")
    return config, info
