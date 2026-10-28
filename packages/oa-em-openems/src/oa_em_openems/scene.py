# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""EMProblem to openEMS scene: geometry primitives, materials, mesh lines, ports, boundaries.

The scene is a plain JSON document (lengths in micrometres, ``unit = 1e-6``) that the generated
script ``sim.py`` turns into CSXCAD/openEMS calls. Building it needs neither openEMS nor CSXCAD,
so geometry, mesh and port translation are unit-testable anywhere.

Modelling choices (all documented in the README):

- Copper is a zero-thickness ``ConductingSheet`` (default; carries the conductivity and the real
  thickness) or a thick PEC body (``openems.conductor_model = "pec"``). The sheet sits on the
  dielectric face of the layer (so the dielectric heights are preserved) or mid-layer when
  dielectric touches both faces.
- Via barrels are solid PEC cylinders of the drill radius.
- Dielectrics are one Debye pole plus conductivity fitted over the simulated band
  (:mod:`oa_em_openems.dispersion`), or constants at band centre (``openems.dispersion``).
- Geometry touching an absorbing region face is extended through the padding (slabs, planes) so
  the boundary does not cut the board; wave ports add their own feed line there.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from oa_em_openems.dispersion import DebyeFit, constant_equivalent, eval_eps, fit_debye
from oa_em_openems.geom2d import keyhole_ring, ring_edge_normals
from oa_em_openems.mesh import (
    AxisMesh,
    estimate_timestep,
    round_lines,
    smooth_axis,
    third_rule_lines,
)
from oa_model.common import Diagnostic
from oa_model.em import (
    BC,
    ConductorBody,
    EMCoaxPort,
    EMLumpedPort,
    EMProblem,
    EMTerminalGeom,
    EMWavePort,
    FullWaveDriven,
    LumpedElement,
    MaterialSlab,
    ViaBody,
)
from oa_model.materials import ConductorMaterial, DielectricMaterial

C0 = 299792458.0
UNIT = 1e-6  # drawing unit of the scene [m]
SCENE_SCHEMA = "oa-openems-scene/1"
_AX = {"x": 0, "y": 1, "z": 2}
_FACES = ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")
_PRESET_CPW = {"coarse": 10.0, "medium": 20.0, "fine": 30.0}
_EPS = 1e-9


@dataclass(frozen=True)
class Options:
    """Adapter options. They come from ``EMProblem.mesh.expert`` keys prefixed ``openems.``."""

    conductor_model: str = "sheet"  # "sheet" | "pec"
    dispersion: str = "debye"  # "debye" | "constant"
    absorbing: str = "PML_8"  # openEMS boundary string used for "absorbing"
    port_length: float | None = None  # wave-port feed length [m]; None = automatic
    pulse: str = "bandpass"  # "bandpass" (no DC content) | "baseband" (tutorial Gaussian 0..f_max)
    farfield: bool = False  # the far-field flag (see OpenEMSAdapter)

    @classmethod
    def from_problem(cls, problem: EMProblem, *, farfield: bool = False) -> Options:
        ex = problem.mesh.expert
        port_length = ex.get("openems.port_length")
        return cls(
            conductor_model=str(ex.get("openems.conductor_model", "sheet")),
            dispersion=str(ex.get("openems.dispersion", "debye")),
            absorbing=str(ex.get("openems.absorbing", "PML_8")),
            port_length=float(port_length) if isinstance(port_length, (int, float)) else None,
            pulse=str(ex.get("openems.pulse", "bandpass")),
            farfield=bool(ex.get("openems.farfield", farfield)),
        )


@dataclass
class Scene:
    """Result of the translation."""

    data: dict[str, Any]
    mesh: dict[str, AxisMesh]
    stats: dict[str, Any]
    diagnostics: list[Diagnostic] = field(default_factory=list)


def um(v: float) -> float:
    """Metres to scene units (micrometres) rounded to 1 nm."""
    return float(round(v / UNIT, 3)) + 0.0


def frequency_grid(problem: EMProblem) -> np.ndarray:
    """Analysis frequencies [Hz]: ``f_min`` (or the first non-DC point) to ``f_max`` plus extras."""
    fp = problem.frequency
    n = fp.n_points
    f_lo = fp.f_min if fp.f_min > 0 else fp.f_max / max(n - 1, 1)
    f_lo = min(f_lo, fp.f_max * (1 - 1e-6))
    grid = (
        np.geomspace(f_lo, fp.f_max, n) if fp.spacing == "log" else np.linspace(f_lo, fp.f_max, n)
    )
    extra = np.array(fp.extra_points, dtype=np.float64)
    return np.unique(np.concatenate([grid, extra]))


def _stable_hash(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass
class _Body:
    cb: ConductorBody
    sheet_z: float
    rings: list[tuple[np.ndarray, list[np.ndarray]]]  # (exterior, holes) in metres, extended
    conductivity: float


def _material_maps(problem: EMProblem) -> tuple[dict[str, DielectricMaterial], dict[str, float]]:
    diel: dict[str, DielectricMaterial] = {}
    cond: dict[str, float] = {}
    for m in problem.structure.materials:
        if isinstance(m, DielectricMaterial):
            diel[m.name] = m
        elif isinstance(m, ConductorMaterial):
            cond[m.name] = m.conductivity
    return diel, cond


def _sheet_z(cb: ConductorBody, slabs: tuple[MaterialSlab, ...]) -> float:
    below = any(abs(s.z_top - cb.z_bottom) < _EPS for s in slabs)
    above = any(abs(s.z_bottom - cb.z_top) < _EPS for s in slabs)
    if below and not above:
        return cb.z_bottom
    if above and not below:
        return cb.z_top
    return 0.5 * (cb.z_bottom + cb.z_top)


def face_axis(face: str) -> tuple[int, int, int]:
    """``(propagation axis, transverse axis, side)`` of a wave-port face; side -1 = min face."""
    return (
        0 if face[0] == "x" else 1,
        1 if face[0] == "x" else 0,
        -1 if face.endswith("min") else 1,
    )


def _terminal_axis(p: EMTerminalGeom, n: EMTerminalGeom) -> int:
    d = [abs(p.position[0] - n.position[0]), abs(p.position[1] - n.position[1]), abs(p.z - n.z)]
    return int(np.argmax(d))


def _xyz(t: EMTerminalGeom) -> tuple[float, float, float]:
    return (t.position[0], t.position[1], t.z)


@dataclass
class _WavePortGeom:
    kind: str  # "msl" | "stripline"
    prop: int
    trans: int
    side: int
    face_pos: float
    c0: float
    c1: float
    strip_z: float
    ref_z: float
    ref_z2: float | None  # second ground (stripline)
    sig_ids: tuple[str, ...]


def derive_wave_port(
    port: EMWavePort, problem: EMProblem, bodies: dict[str, _Body]
) -> tuple[_WavePortGeom | None, list[Diagnostic]]:
    """Strip extent, strip height and reference-plane heights of a wave port from the geometry."""
    diags: list[Diagnostic] = []
    prop, trans, side = face_axis(port.face)
    box = problem.structure.bbox
    lo = (box.xmin, box.ymin)[prop]
    hi = (box.xmax, box.ymax)[prop]
    face_pos = lo if side < 0 else hi

    def err(code: str, msg: str, hint: str) -> tuple[None, list[Diagnostic]]:
        diags.append(
            Diagnostic(severity="error", code=code, message=f"port {port.name}: {msg}", hint=hint)
        )
        return None, diags

    sig = [bodies[i] for i in port.signal_conductors if i in bodies]
    if len(sig) != len(port.signal_conductors) or not sig:
        return err(
            "OPENEMS.PORT_NO_SIGNAL",
            "signal conductor id not found in the structure",
            "List ConductorBody ids that exist in Structure3D.conductors.",
        )
    if len(sig) > 1 or port.n_modes > 1:
        return err(
            "OPENEMS.PORT_MULTI_MODE",
            "multi-conductor/multi-mode wave ports are not supported by the TL-probe ports",
            "Use one signal conductor and n_modes=1, or the Palace adapter for coupled lines.",
        )
    coords: list[float] = []
    for body in sig:
        for ext, _holes in body.rings:
            arr = ext
            on_face = np.abs(arr[:, prop] - face_pos) < 1e-7
            coords.extend(float(v) for v in arr[on_face, trans])
    if not coords:
        return err(
            "OPENEMS.PORT_NOT_ON_FACE",
            "signal conductor has no vertices on the port face",
            "Cut the region so the trace crosses the port face.",
        )
    c0, c1 = min(coords), max(coords)
    strip = sig[0]
    strip_z = strip.sheet_z

    def spans(b: _Body) -> bool:
        for ext, _h in b.rings:
            if (
                ext[:, prop].min() - 1e-7 <= face_pos <= ext[:, prop].max() + 1e-7
                and ext[:, trans].min() - 1e-7 <= c0
                and ext[:, trans].max() + 1e-7 >= c1
            ):
                return True
        return False

    refs = [b for b in bodies.values() if b.cb.role == "reference" and spans(b)]
    below = sorted(
        (b for b in refs if b.sheet_z < strip_z - _EPS), key=lambda b: strip_z - b.sheet_z
    )
    above = sorted(
        (b for b in refs if b.sheet_z > strip_z + _EPS), key=lambda b: b.sheet_z - strip_z
    )
    lt = port.line_type
    if lt == "general":
        lt = "stripline" if below and above else "microstrip"
    if lt == "microstrip":
        near = below + above
        if not near:
            return err(
                "OPENEMS.PORT_NO_REFERENCE",
                "no reference plane under/over the strip at the port face",
                "Mark the return plane ConductorBody role='reference' and extend it to the face.",
            )
        nearest = min(near, key=lambda b: abs(b.sheet_z - strip_z))
        return (
            _WavePortGeom(
                "msl",
                prop,
                trans,
                side,
                face_pos,
                c0,
                c1,
                strip_z,
                nearest.sheet_z,
                None,
                port.signal_conductors,
            ),
            diags,
        )
    if lt == "stripline":
        if not (below and above):
            return err(
                "OPENEMS.PORT_NO_REFERENCE",
                "stripline port needs a reference plane above and below the strip",
                "Check conductor roles, or set line_type='microstrip'.",
            )
        h1, h2 = strip_z - below[0].sheet_z, above[0].sheet_z - strip_z
        if abs(h1 - h2) > 0.05 * max(h1, h2):
            return err(
                "OPENEMS.PORT_ASYMMETRIC_STRIPLINE",
                f"asymmetric stripline ({h1 * 1e6:.1f} um / {h2 * 1e6:.1f} um) is not supported",
                "openEMS StripLinePort assumes a centred strip; use a microstrip-style port or "
                "the Palace adapter.",
            )
        return (
            _WavePortGeom(
                "stripline",
                prop,
                trans,
                side,
                face_pos,
                c0,
                c1,
                strip_z,
                below[0].sheet_z,
                above[0].sheet_z,
                port.signal_conductors,
            ),
            diags,
        )
    return err(
        "OPENEMS.PORT_LINE_TYPE",
        f"line_type {port.line_type!r} is not supported",
        "Use microstrip, stripline or general.",
    )


def check_problem(problem: EMProblem, *, farfield_enabled: bool = False) -> list[Diagnostic]:
    """Unsupported features as diagnostics (errors block ``prepare``)."""
    out: list[Diagnostic] = []
    if not isinstance(problem.physics, FullWaveDriven):
        out.append(
            Diagnostic(
                severity="error",
                code="OPENEMS.PHYSICS",
                message=f"physics {problem.physics.kind!r} is not supported by openEMS",
                hint="openEMS solves fullwave_driven problems only.",
            )
        )
    if not problem.ports:
        out.append(
            Diagnostic(
                severity="error",
                code="OPENEMS.NO_PORTS",
                message="the problem has no ports",
                hint="Add at least one lumped or wave port.",
            )
        )
    if not all(getattr(p, "excite", True) for p in problem.ports):
        out.append(
            Diagnostic(
                severity="error",
                code="OPENEMS.NO_EXCITATION",
                message="every port needs excite=True: S_jj of an unexcited port cannot be derived",
                hint="Set excite=True on all ports (one FDTD run per port).",
            )
        )
    for p in problem.ports:
        if isinstance(p, EMCoaxPort):
            out.append(
                Diagnostic(
                    severity="error",
                    code="OPENEMS.PORT_COAX",
                    message=f"port {p.name}: coax ports are not implemented in this adapter",
                    hint="Use a lumped port or the Palace adapter.",
                )
            )
        elif isinstance(p, EMWavePort) and getattr(problem.boundaries, p.face) not in (
            "absorbing",
            "pml",
        ):
            out.append(
                Diagnostic(
                    severity="error",
                    code="OPENEMS.PORT_FACE_BOUNDARY",
                    message=f"port {p.name}: wave-port face {p.face} must be absorbing",
                    hint="Set the boundary of the port face to 'absorbing' (the feed line runs "
                    "into the absorber).",
                )
            )
        elif isinstance(p, EMWavePort) and p.line_type in ("cpw", "cpwg"):
            out.append(
                Diagnostic(
                    severity="error",
                    code="OPENEMS.PORT_CPW",
                    message=f"port {p.name}: CPW wave ports are not implemented in this adapter",
                    hint="Use microstrip/stripline ports or the Palace adapter.",
                )
            )
    if any(c.roughness.kind != "none" for c in problem.structure.conductors):
        out.append(
            Diagnostic(
                severity="warning",
                code="OPENEMS.ROUGHNESS_IGNORED",
                message="openEMS has no surface-roughness model; roughness is ignored",
                hint="Raise the conductor resistivity to an effective value if loss matters.",
            )
        )
    if problem.outputs.farfield is not None and not farfield_enabled:
        out.append(
            Diagnostic(
                severity="error",
                code="OPENEMS.FARFIELD_DISABLED",
                message="far-field output requested but far-field support is disabled",
                hint="Enable it with OpenEMSAdapter(farfield=True) or expert 'openems.farfield'.",
            )
        )
    if problem.frequency.adaptive:
        out.append(
            Diagnostic(
                severity="info",
                code="OPENEMS.ADAPTIVE_IGNORED",
                message="adaptive sweep is an FEM feature; FDTD computes the whole band at once",
            )
        )
    return out


def _bc_string(bc: BC, absorbing: str) -> str:
    return {"pec": "PEC", "pmc": "PMC", "absorbing": absorbing, "pml": "PML_8"}[bc]


def build_scene(problem: EMProblem, options: Options | None = None) -> Scene:
    """Translate ``problem`` into the openEMS scene. Raises ``ValueError`` on unsupported input."""
    opts = options or Options.from_problem(problem)
    errors = [
        d for d in check_problem(problem, farfield_enabled=opts.farfield) if d.severity == "error"
    ]
    if errors:
        raise ValueError("; ".join(f"{d.code}: {d.message}" for d in errors))
    diags: list[Diagnostic] = [
        d for d in check_problem(problem, farfield_enabled=opts.farfield) if d.severity != "error"
    ]
    st = problem.structure
    box = st.bbox
    f_grid = frequency_grid(problem)
    f_max = float(problem.frequency.f_max)
    f_lo = float(f_grid[0])
    diel, cond_sigma = _material_maps(problem)

    # ---- dielectrics ------------------------------------------------------------------------
    used = sorted({s.material for s in st.slabs})
    missing = [m for m in used if m not in diel]
    if missing:
        raise ValueError(f"slab materials not defined as dielectrics: {missing}")
    materials: list[dict[str, Any]] = []
    fits: dict[str, dict[str, float]] = {}
    eps_hi_f = 1.0
    f_centre = 0.5 * (f_lo + f_max)
    for name in used:
        m = diel[name]
        ratio = m.dk_z_over_dk_xy
        model = m.model
        eps_at_fmax = float(eval_eps(model, np.array([f_max]))[0].real)
        eps_hi_f = max(eps_hi_f, eps_at_fmax, eps_at_fmax * ratio)
        rec: dict[str, Any] = {"name": name}
        if opts.dispersion == "debye" and model.kind != "constant":
            fit: DebyeFit = fit_debye(model, max(f_lo, f_max / 20.0), f_max)
            fits[name] = fit.as_dict()
            rec.update(
                epsilon=[
                    round(fit.eps_inf, 9),
                    round(fit.eps_inf, 9),
                    round(fit.eps_inf * ratio, 9),
                ],
                kappa=[round(fit.kappa, 12)] * 2 + [round(fit.kappa * ratio, 12)],
                debye={
                    "eps_delta": [
                        round(fit.delta_eps, 9),
                        round(fit.delta_eps, 9),
                        round(fit.delta_eps * ratio, 9),
                    ],
                    "eps_relax": float(f"{fit.tau:.9e}"),
                },
            )
            if fit.max_rel_err_tand > 0.1 or fit.max_rel_err_eps > 0.02:
                diags.append(
                    Diagnostic(
                        severity="warning",
                        code="OPENEMS.DEBYE_FIT_ERROR",
                        message=(
                            f"single-pole fit of {name}: eps' error {fit.max_rel_err_eps:.1%}, "
                            f"tan(d) error {fit.max_rel_err_tand:.1%} over "
                            f"{fit.f_lo / 1e9:.3g}-{fit.f_hi / 1e9:.3g} GHz"
                        ),
                        hint="Narrow the frequency band or use openems.dispersion='constant'.",
                    )
                )
        else:
            eps_c, kappa = constant_equivalent(model, f_centre)
            rec.update(
                epsilon=[round(eps_c, 9), round(eps_c, 9), round(eps_c * ratio, 9)],
                kappa=[round(kappa, 12)] * 2 + [round(kappa * ratio, 12)],
                debye=None,
            )
            fits[name] = {"constant_at_hz": f_centre, "eps": eps_c, "kappa": kappa}
        materials.append(rec)

    lam_min = C0 / (f_max * math.sqrt(eps_hi_f))
    cpw = problem.mesh.cells_per_wavelength or _PRESET_CPW[problem.mesh.preset]
    d_max = lam_min / cpw
    if problem.mesh.min_cell:
        d_max = max(d_max, problem.mesh.min_cell)
    grading = problem.mesh.max_grading

    # ---- domain extents ---------------------------------------------------------------------
    b = problem.boundaries
    bcs = {f: getattr(b, f) for f in _FACES}
    lam_air = C0 / f_max
    pad = b.air_padding if b.air_padding is not None else lam_air / 4.0
    wave_faces: dict[str, EMWavePort] = {
        p.face: p for p in problem.ports if isinstance(p, EMWavePort)
    }
    # ---- conductors (extended through absorbing faces) --------------------------------------
    bbox_lo = (box.xmin, box.ymin, box.zmin)
    bbox_hi = (box.xmax, box.ymax, box.zmax)
    # the feed-line length needs the strip width and the dielectric height; estimate it first
    widths: list[float] = []
    for cb in st.conductors:
        if cb.role == "signal":
            for poly in cb.polygons:
                a = poly.exterior.array
                widths.append(float(min(np.ptp(a[:, 0]), np.ptp(a[:, 1]))))
    for v in st.vias:
        widths.append(2.0 * v.r_outer)
    w_min = min(widths) if widths else d_max
    d_edge = min(max(w_min / 6.0, d_max / 16.0), d_max / 4.0)
    if problem.mesh.min_cell:
        d_edge = max(d_edge, problem.mesh.min_cell)
    heights = [s.z_top - s.z_bottom for s in st.slabs]
    h_min = min(heights) if heights else d_max
    if opts.port_length is not None:
        l_port = opts.port_length
    else:
        l_port = max(4.0 * (max(widths) if widths else d_max), 6.0 * h_min, 8.0 * d_max)

    ext_lo = [0.0, 0.0, 0.0]
    ext_hi = [0.0, 0.0, 0.0]
    for face in _FACES:
        axis = _AX[face[0]]
        if bcs[face] in ("absorbing", "pml"):
            e = l_port if face in wave_faces else pad
            if face.endswith("min"):
                ext_lo[axis] = e
            else:
                ext_hi[axis] = e
    dom_lo = [bbox_lo[i] - ext_lo[i] for i in range(3)]
    dom_hi = [bbox_hi[i] + ext_hi[i] for i in range(3)]

    # signal bodies end at ports: no extension through that face
    terminated: dict[str, set[str]] = {}
    for p in problem.ports:
        if isinstance(p, EMWavePort):
            for cid in p.signal_conductors:
                terminated.setdefault(cid, set()).add(p.face)
        elif isinstance(p, EMLumpedPort):
            for t in (p.positive, p.negative):
                for cb in st.conductors:
                    if cb.role != "signal" or cb.layer != t.layer:
                        continue
                    for fa in _FACES[:4]:
                        coord = (box.xmin, box.xmax, box.ymin, box.ymax)[_FACES.index(fa)]
                        if abs(t.position[_AX[fa[0]]] - coord) < 1e-7:
                            terminated.setdefault(cb.id, set()).add(fa)

    absorbing_faces = {f for f in _FACES if bcs[f] in ("absorbing", "pml")}

    def ext_pt(x: float, y: float, body_id: str | None, role: str) -> tuple[float, float]:
        """Push a vertex on an absorbing bbox face out to the padded domain face."""
        if role == "floating":
            return x, y
        out = [x, y]
        held = terminated.get(body_id or "", set())
        for axis, val in enumerate((x, y)):
            lo_face, hi_face = _FACES[2 * axis], _FACES[2 * axis + 1]
            if (
                lo_face in absorbing_faces
                and lo_face not in held
                and abs(val - bbox_lo[axis]) < 1e-7
            ):
                out[axis] = val - ext_lo[axis]
            elif (
                hi_face in absorbing_faces
                and hi_face not in held
                and abs(val - bbox_hi[axis]) < 1e-7
            ):
                out[axis] = val + ext_hi[axis]
        return out[0], out[1]

    bodies: dict[str, _Body] = {}
    for cb in st.conductors:
        sigma = cond_sigma.get(cb.material, 5.8e7)
        rings: list[tuple[np.ndarray, list[np.ndarray]]] = []
        for poly in cb.polygons:
            ex = np.array([ext_pt(x, y, cb.id, cb.role) for x, y in poly.exterior.array])
            hs = [np.array([ext_pt(x, y, cb.id, cb.role) for x, y in h.array]) for h in poly.holes]
            rings.append((ex, hs))
        bodies[cb.id] = _Body(cb, _sheet_z(cb, st.slabs), rings, sigma)

    # ---- ports ------------------------------------------------------------------------------
    port_geoms: dict[str, _WavePortGeom] = {}
    for p in problem.ports:
        if isinstance(p, EMWavePort):
            g, d = derive_wave_port(p, problem, bodies)
            diags.extend(d)
            if g is None:
                raise ValueError(
                    "; ".join(f"{x.code}: {x.message}" for x in d if x.severity == "error")
                )
            port_geoms[p.name] = g

    # ---- fixed mesh lines -------------------------------------------------------------------
    seeds: list[list[tuple[float, float]]] = [[], [], []]
    hard: list[list[float]] = [[], [], []]
    locked_z: list[float] = []

    def add(axis: int, pos: float, size: float, *, is_hard: bool = False) -> None:
        seeds[axis].append((pos, size))
        if is_hard:
            hard[axis].append(pos)

    for axis in range(3):
        add(axis, dom_lo[axis], d_max, is_hard=True)
        add(axis, dom_hi[axis], d_max, is_hard=True)
        if axis < 2:
            add(axis, bbox_lo[axis], min(d_max, 2 * d_edge), is_hard=True)
            add(axis, bbox_hi[axis], min(d_max, 2 * d_edge), is_hard=True)

    # ports first (their planes are hard lines the third rule must not crowd)
    port_scene: list[dict[str, Any]] = []
    for idx, p in enumerate(problem.ports):
        nr = idx + 1
        if isinstance(p, EMLumpedPort):
            ax = _terminal_axis(p.positive, p.negative)
            pos_p, neg_p = list(_xyz(p.positive)), list(_xyz(p.negative))
            start, stop = list(neg_p), list(pos_p)
            for tax in range(3):
                if tax == ax:
                    continue
                mid = 0.5 * (pos_p[tax] + neg_p[tax])
                start[tax] = stop[tax] = mid
            # transverse extent: the in-plane axis perpendicular to the terminal line, else patch
            trans_ax = next((t for t in (1, 0) if t != ax and t < 2), 1)
            ext_u = max(p.positive.extent[0], p.negative.extent[0])
            ext_v = max(p.positive.extent[1], p.negative.extent[1])
            if ax == 2:
                if ext_u > 0 and ext_v == 0:
                    trans_ax = 0
                half = 0.5 * p.width
                start[trans_ax] -= half
                stop[trans_ax] += half
                if max(ext_u, ext_v) > 0 and ext_u > 0 and ext_v > 0:
                    start[0] -= 0.5 * ext_u
                    stop[0] += 0.5 * ext_u
            else:
                trans_ax = 1 if ax == 0 else 0
                half = 0.5 * p.width
                start[trans_ax] -= half
                stop[trans_ax] += half
            for tax in range(3):
                if start[tax] == stop[tax] or tax in (ax, 2):  # sheet plane and terminal levels
                    add(tax, min(start[tax], stop[tax]), d_edge, is_hard=True)
                    if start[tax] != stop[tax]:
                        add(tax, max(start[tax], stop[tax]), d_edge, is_hard=True)
            port_scene.append(
                {
                    "nr": nr,
                    "name": p.name,
                    "type": "lumped",
                    "R": p.z0,
                    "start": [um(v) for v in start],
                    "stop": [um(v) for v in stop],
                    "exc_dir": "xyz"[ax],
                    "z0": p.z0,
                    "ref_shift": 0.0,
                }
            )
        elif isinstance(p, EMWavePort):
            g = port_geoms[p.name]
            lo_a = dom_lo[g.prop] if g.side < 0 else bbox_hi[g.prop]
            hi_a = bbox_lo[g.prop] if g.side < 0 else dom_hi[g.prop]
            outer, inner = (lo_a, hi_a) if g.side < 0 else (hi_a, lo_a)
            start = [0.0, 0.0, 0.0]
            stop = [0.0, 0.0, 0.0]
            start[g.prop], stop[g.prop] = outer, inner
            start[g.trans], stop[g.trans] = g.c0, g.c1
            start[2] = g.strip_z
            stop[2] = g.ref_z if g.kind == "msl" else g.strip_z
            length = abs(outer - inner)
            add(g.prop, g.face_pos, d_edge, is_hard=True)
            add(2, g.strip_z, d_edge, is_hard=True)
            add(2, g.ref_z, d_edge, is_hard=True)
            if g.ref_z2 is not None:
                add(2, g.ref_z2, d_edge, is_hard=True)
            body0 = bodies[g.sig_ids[0]]
            port_scene.append(
                {
                    "nr": nr,
                    "name": p.name,
                    "type": g.kind,
                    "start": [um(v) for v in start],
                    "stop": [um(v) for v in stop],
                    "prop_dir": "xyz"[g.prop],
                    "exc_dir": "z",
                    "height": um(abs(g.ref_z - g.strip_z)),
                    "feed_shift": um(0.2 * length),
                    "meas_shift": um(0.6 * length),
                    "ref_shift": um(length + p.deembed),
                    "metal": _metal_name(body0, opts),
                    "z0": p.z0,
                    "length": um(length),
                }
            )

    # ---- z lines: dielectric stack and conductors --------------------------------------------
    breaks: set[float] = set()
    for body in bodies.values():
        if opts.conductor_model == "sheet":
            breaks.add(body.sheet_z)
        else:
            breaks.update((body.cb.z_bottom, body.cb.z_top))
    for v in st.vias:
        breaks.update((v.z_bottom, v.z_top))
    for le in st.lumped:
        breaks.update((le.positive.z, le.negative.z))
    for p in problem.ports:
        if isinstance(p, EMLumpedPort):
            breaks.update((p.positive.z, p.negative.z))
    for z in breaks:
        add(2, z, d_edge)

    def inside_conductor(za: float, zb: float) -> bool:
        return any(
            b2.cb.z_bottom - _EPS <= za and zb <= b2.cb.z_top + _EPS for b2 in bodies.values()
        )

    regions: list[list[float]] = []  # merged same-material dielectric, with resin in the gaps
    ordered = sorted(st.slabs, key=lambda sl: (sl.z_bottom, sl.z_top))
    for k, sl in enumerate(ordered):
        if sl.footprint is None and regions and k > 0:
            prev = ordered[k - 1]
            if prev.footprint is None and prev.material == sl.material:
                regions[-1][1] = max(regions[-1][1], sl.z_top)
                continue
        regions.append([sl.z_bottom, sl.z_top])
    for zb, zt in regions:
        pts = sorted({zb, zt, *(z for z in breaks if zb + _EPS < z < zt - _EPS)})
        for lo_z, hi_z in itertools.pairwise(pts):
            span = hi_z - lo_z
            n = 1 if inside_conductor(lo_z, hi_z) else max(4, math.ceil(span / d_max))
            for kk in range(n + 1):
                add(2, lo_z + kk * span / n, min(span / n, d_max))
                locked_z.append(lo_z + kk * span / n)

    # ---- xy lines: metal edges (third rule), vias ---------------------------------------------
    def hard_near(axis: int, pos: float) -> bool:
        return any(abs(pos - h) < 1.5 * d_edge for h in hard[axis])

    def metal_edge(axis: int, pos: float, outward: float) -> None:
        if hard_near(axis, pos) or min(abs(pos - dom_lo[axis]), abs(pos - dom_hi[axis])) < 1e-9:
            return
        a, b2 = third_rule_lines(pos, outward, d_edge)
        add(axis, a, d_edge)
        add(axis, b2, d_edge)

    seen_edges: set[tuple[int, float, float]] = set()
    for body in bodies.values():
        for ex, holes in body.rings:
            for ring in (ex, *holes):
                bb = (ring[:, 0].min(), ring[:, 0].max(), ring[:, 1].min(), ring[:, 1].max())
                for e0, e1, nrm in ring_edge_normals(ring):
                    if abs(e0[0] - e1[0]) < 1e-12:  # vertical edge: x = const
                        key = (0, round(e0[0], 9), round(nrm[0], 1))
                        if key not in seen_edges:
                            seen_edges.add(key)
                            metal_edge(0, e0[0], nrm[0])
                    elif abs(e0[1] - e1[1]) < 1e-12:
                        key = (1, round(e0[1], 9), round(nrm[1], 1))
                        if key not in seen_edges:
                            seen_edges.add(key)
                            metal_edge(1, e0[1], nrm[1])
                    else:
                        # slanted/curved: mesh its bounding extremes once per ring
                        for axis, lo_v, hi_v in ((0, bb[0], bb[1]), (1, bb[2], bb[3])):
                            for val, out in ((lo_v, -1.0), (hi_v, 1.0)):
                                key = (axis, round(val, 9), out)
                                if key in seen_edges:
                                    continue
                                seen_edges.add(key)
                                # hole rings have material outside, outer rings inside
                                metal_edge(axis, val, out if ring is ex else -out)
    for v in st.vias:
        r = v.r_outer
        for axis in (0, 1):
            centre = v.center[axis]
            for step in (-2, -1, 0, 1, 2):
                add(axis, centre + step * r / 2.0, r / 2.0)

    # ---- mesh --------------------------------------------------------------------------------
    mesh: dict[str, AxisMesh] = {}
    final: dict[str, np.ndarray] = {}
    for axis, name in enumerate("xyz"):
        clipped = [
            (p, s) for p, s in seeds[axis] if dom_lo[axis] - 1e-12 <= p <= dom_hi[axis] + 1e-12
        ]
        am = smooth_axis(clipped, d_max, grading, locked_z if axis == 2 else ())
        mesh[name] = am
        final[name] = round_lines(am.lines, UNIT)

    # ---- primitives --------------------------------------------------------------------------
    prims: list[dict[str, Any]] = []
    props: dict[str, dict[str, Any]] = {}
    for mrec in materials:
        props[mrec["name"]] = {"kind": "material", **{k: v for k, v in mrec.items() if k != "name"}}

    def box_prim(prop: str, prio: int, lo: list[float], hi: list[float]) -> dict[str, Any]:
        return {
            "shape": "box",
            "prop": prop,
            "priority": prio,
            "start": [um(v) for v in lo],
            "stop": [um(v) for v in hi],
        }

    def ext_z(z: float) -> float:
        if ext_lo[2] > 0 and abs(z - bbox_lo[2]) < 1e-7:
            return z - ext_lo[2]
        if ext_hi[2] > 0 and abs(z - bbox_hi[2]) < 1e-7:
            return z + ext_hi[2]
        return z

    # fill resin between slabs where a conductor layer sits inside the stack
    full = sorted((s for s in st.slabs if s.footprint is None), key=lambda s: (s.z_bottom, s.z_top))
    for lower, upper in itertools.pairwise(full):
        if upper.z_bottom - lower.z_top > _EPS:
            prims.append(
                box_prim(
                    lower.material,
                    0,
                    [dom_lo[0], dom_lo[1], lower.z_top],
                    [dom_hi[0], dom_hi[1], upper.z_bottom],
                )
            )
    for s in st.slabs:
        zb, zt = ext_z(s.z_bottom), ext_z(s.z_top)
        if s.footprint is None:
            prims.append(
                box_prim(s.material, 1, [dom_lo[0], dom_lo[1], zb], [dom_hi[0], dom_hi[1], zt])
            )
        else:
            fp_pts = [ext_pt(fx, fy, None, "reference") for fx, fy in s.footprint.exterior.array]
            prims.append(
                {
                    "shape": "linpoly",
                    "prop": s.material,
                    "priority": 1,
                    "points": [[um(fx), um(fy)] for fx, fy in fp_pts],
                    "elevation": um(zb),
                    "length": um(zt - zb),
                }
            )

    pec_name = "PEC"
    for body in bodies.values():
        cb = body.cb
        thickness = cb.z_top - cb.z_bottom
        sheet = opts.conductor_model == "sheet" and thickness > 0 and body.conductivity < 1e12
        if sheet:
            pname = f"CU_{body.conductivity:.6g}_{thickness * 1e6:.4g}um"
            props[pname] = {
                "kind": "sheet",
                "conductivity": body.conductivity,
                "thickness": float(f"{thickness:.9g}"),
            }
        else:
            pname = pec_name
            props[pec_name] = {"kind": "metal"}
        for ex, holes in body.rings:
            kh = keyhole_ring(
                [(float(px), float(py)) for px, py in ex],
                [[(float(px), float(py)) for px, py in hole] for hole in holes],
            )
            ring_pts = [[um(px), um(py)] for px, py in kh]
            if sheet:
                prims.append(
                    {
                        "shape": "polygon",
                        "prop": pname,
                        "priority": 10,
                        "points": ring_pts,
                        "elevation": um(body.sheet_z),
                    }
                )
            else:
                prims.append(
                    {
                        "shape": "linpoly",
                        "prop": pname,
                        "priority": 10,
                        "points": ring_pts,
                        "elevation": um(cb.z_bottom),
                        "length": um(thickness),
                    }
                )
    for v in st.vias:
        props[pec_name] = {"kind": "metal"}
        prims.append(_via_prim(v))

    # lumped elements
    for le in st.lumped:
        prims_name, prim = _lumped_element(le)
        props[prims_name] = prim["prop"]
        prims.append(prim["prim"])

    # ---- boundaries / excitation -------------------------------------------------------------
    boundary = [_bc_string(bcs[f], opts.absorbing) for f in _FACES]
    f0, fc = _pulse(opts.pulse, problem.frequency.f_min, f_max)
    end_crit = float(10.0 ** (problem.convergence.energy_decay_db / 10.0))

    nx, ny, nz = (final[a].size for a in "xyz")
    cells = (nx - 1) * (ny - 1) * (nz - 1)
    pml_cells = 0
    n_dir = (nx - 1, ny - 1, nz - 1)
    pml_layers = 8
    for fi in range(len(_FACES)):
        if boundary[fi].startswith("PML"):
            ax = fi // 2
            others = [n_dir[k] for k in range(3) if k != ax]
            pml_cells += pml_layers * others[0] * others[1]
    dmin = [float(np.min(np.diff(final[a]))) * UNIT for a in "xyz"]
    dt = estimate_timestep(*dmin)
    path_len = (dom_hi[0] - dom_lo[0]) + (dom_hi[1] - dom_lo[1])
    t_sim = 4.0 / max(fc, 1.0) * 3.0 + 6.0 * path_len * math.sqrt(eps_hi_f) / C0
    steps_est = int(min(problem.convergence.max_timesteps, t_sim / dt))
    mem_gb = (cells + pml_cells) * 150.0 / 1e9 * 1.5
    excited = [i + 1 for i, p in enumerate(problem.ports) if p.excite]
    stats: dict[str, Any] = {
        "cells": int(cells),
        "pml_cells": int(pml_cells),
        "lines": {"x": int(nx), "y": int(ny), "z": int(nz)},
        "min_cell_um": [round(d / UNIT, 3) for d in dmin],
        "max_cell_um": [round(float(np.max(np.diff(final[a]))), 3) for a in "xyz"],
        "max_grading": [round(mesh[a].max_grading, 3) for a in "xyz"],
        "d_max_um": round(d_max / UNIT, 3),
        "d_edge_um": round(d_edge / UNIT, 3),
        "cells_per_wavelength": cpw,
        "lambda_min_um": round(lam_min / UNIT, 3),
        "port_length_um": round(l_port / UNIT, 3),
        "dt_s": dt,
        "est_timesteps": steps_est,
        "est_memory_gb": round(mem_gb, 4),
        "est_time_s": round((cells + pml_cells) * steps_est / 5e7, 2),
        "n_runs": len(excited),
        "dielectric_fits": fits,
    }
    mesh_hash = _stable_hash({a: final[a].tolist() for a in "xyz"})
    stats["mesh_hash"] = mesh_hash[:16]
    data: dict[str, Any] = {
        "schema": SCENE_SCHEMA,
        "unit": UNIT,
        "name": problem.name,
        "mesh": {a: final[a].tolist() for a in "xyz"},
        "boundary": boundary,
        "excitation": {"f0": f0, "fc": fc},
        "end_criteria": end_crit,
        "max_timesteps": int(problem.convergence.max_timesteps),
        "freqs": [float(f"{f:.9e}") for f in f_grid],
        "properties": props,
        "primitives": prims,
        "ports": port_scene,
        "excited": None,
        "nf2ff": _nf2ff(problem) if (problem.outputs.farfield and opts.farfield) else None,
    }
    return Scene(data=data, mesh=mesh, stats=stats, diagnostics=diags)


def _pulse(kind: str, f_min: float, f_max: float) -> tuple[float, float]:
    """Gaussian excitation ``(f0, fc)``: centre and 20 dB half-bandwidth [Hz].

    The default has less than -60 dB of DC content (``f0 = sqrt(3) fc``). A pulse with DC content
    leaves static charge on strips that no port drains (closed stripline boxes), so the energy
    end criterion would never be met. The price: below about ``0.1 f_max`` the excitation is more
    than 40 dB down. ``baseband`` is the openEMS tutorial pulse (0 .. f_max).
    """
    if kind == "baseband":
        return round(0.5 * f_max, 3), round(0.5 * f_max, 3)
    fc = f_max / (1.0 + math.sqrt(3.0))
    f0 = math.sqrt(3.0) * fc
    if f_min > f0 - fc:
        f0, fc = 0.5 * (f_min + f_max), 0.5 * (f_max - f_min)
    return round(f0, 3), round(fc, 3)


def _nf2ff(problem: EMProblem) -> dict[str, Any] | None:
    ff = problem.outputs.farfield
    if ff is None:
        return None
    return {
        "freqs": [float(f) for f in ff.frequencies],
        "theta_step_deg": round(math.degrees(ff.theta_step), 6),
        "phi_step_deg": round(math.degrees(ff.phi_step), 6),
    }


def _metal_name(body: _Body, opts: Options) -> str:
    cb = body.cb
    thickness = cb.z_top - cb.z_bottom
    if opts.conductor_model == "sheet" and thickness > 0 and body.conductivity < 1e12:
        return f"CU_{body.conductivity:.6g}_{thickness * 1e6:.4g}um"
    return "PEC"


def _via_prim(v: ViaBody) -> dict[str, Any]:
    return {
        "shape": "cylinder",
        "prop": "PEC",
        "priority": 10,
        "start": [um(v.center[0]), um(v.center[1]), um(v.z_bottom)],
        "stop": [um(v.center[0]), um(v.center[1]), um(v.z_top)],
        "radius": um(v.r_outer),
    }


def _lumped_element(le: LumpedElement) -> tuple[str, dict[str, Any]]:
    ax = _terminal_axis(le.positive, le.negative)
    p, n = list(_xyz(le.positive)), list(_xyz(le.negative))
    start, stop = list(n), list(p)
    for tax in range(3):
        if tax != ax:
            mid = 0.5 * (p[tax] + n[tax])
            half = 0.5 * (le.positive.extent[tax] if tax < 2 else 0.0)
            start[tax], stop[tax] = mid - half, mid + half
    name = f"LUMPED_{le.id}"
    prop: dict[str, Any] = {
        "kind": "lumped",
        "ny": "xyz"[ax],
        "series": le.topology == "series",
        "R": le.r,
        "L": le.l,
        "C": le.c,
    }
    prim = {
        "shape": "box",
        "prop": name,
        "priority": 15,
        "start": [um(v) for v in start],
        "stop": [um(v) for v in stop],
    }
    return name, {"prop": prop, "prim": prim}


def mesh_summary(scene: Scene) -> dict[str, Any]:
    """Compact, deterministic summary of the mesh for snapshots."""
    out: dict[str, Any] = {}
    for a in "xyz":
        lines = np.asarray(scene.data["mesh"][a])
        d = np.diff(lines)
        out[a] = {
            "n_lines": int(lines.size),
            "first": float(lines[0]),
            "last": float(lines[-1]),
            "min_cell": float(d.min()),
            "max_cell": float(d.max()),
        }
    out["cells"] = scene.stats["cells"]
    out["mesh_hash"] = scene.stats["mesh_hash"]
    return out
