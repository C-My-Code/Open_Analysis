# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Region -> ``EMProblem``: region cut, port resolution and boundary selection (solver-neutral)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import shapely

from oa_em.config import BoundaryConfig, EmSparamsConfig
from oa_geometry import BoardGeometry, RegionCut, RegionSpec
from oa_model.arrays import Points
from oa_model.common import Diagnostic
from oa_model.em import (
    Boundaries,
    ConductorBody,
    EMCoaxPort,
    EMLumpedPort,
    EMPort,
    EMProblem,
    EMWavePort,
    Structure3D,
)
from oa_model.errors import SetupError
from oa_model.geometry import Polygon
from oa_model.setup.ports import CoaxPort, LumpedPort, TraceEndPort

C0 = 299_792_458.0
FACES = ("xmin", "xmax", "ymin", "ymax")
_AXIS_TOL = math.radians(1.0)
# Solver specific defaults for problems with wave ports. openEMS: with its PML the energy of
# TL-probe microstrip/stripline runs plateaus near -31 dB (so S-parameters carry ~-30 dB noise and
# the run only stops at the time-step cap); the Mur ABC converges to -45 dB in seconds and gives
# S21 within 0.3 dB of the analytic line (WP-205 measurements; WP-108 gap).
_WAVE_PORT_DEFAULTS: dict[str, dict[str, Any]] = {"openems": {"openems.absorbing": "MUR"}}
# FDTD pulses carry little energy below ~0.1 f_max (the adapter's DC-free Gaussian), so S-parameters
# below that are noise (S11 -11 dB, RQM 93 on the via transition from 0.2 GHz). The sweep starts
# there instead; the quality gate extrapolates the missing DC point (f_min <= 10 % of f_max).
_FMIN_FRACTION = {"openems": 0.1}

__all__ = ["Built", "PortInfo", "build_problem", "choose_boundaries"]


@dataclass
class PortInfo:
    """Bookkeeping of one ``EMPort`` for the sidecar, the port-Z check and mixed-mode."""

    em_name: str
    setup_name: str
    kind: str  # "wave" | "lumped" | "coax"
    nets: tuple[str, ...] = ()
    leg: str | None = None  # "P" / "N" for differential legs
    face: str | None = None
    layer: str | None = None
    position: tuple[float, float] = (0.0, 0.0)
    seg_id: str | None = None
    extended: bool = False  # trace extended from a free end to the region face
    z0_cfg: float | None = None
    modes: str = "single"


@dataclass
class Built:
    problem: EMProblem
    ports: list[PortInfo]
    cut: RegionCut
    diagnostics: list[Diagnostic] = field(default_factory=list)
    pairs: tuple[tuple[int, int], ...] = ()  # differential (P, N) port index pairs
    want_mixed_mode: bool = False


@dataclass
class _Cand:
    net: str
    face: str
    layer: str
    pos: tuple[float, float]  # on the face (crossing) or the free end (leaf)
    width: float
    seg_id: str
    leaf: bool


def _err(code: str, msg: str, hint: str, **kw: Any) -> Diagnostic:
    return Diagnostic(severity="error", code=code, message=msg, hint=hint, **kw)


def _face_normal_axis(face: str) -> int:
    return 0 if face[0] == "x" else 1


def _face_coord(bbox: tuple[float, float, float, float], face: str) -> float:
    xmin, ymin, xmax, ymax = bbox
    return {"xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax}[face]


def _axis_face(direction: tuple[float, float]) -> str | None:
    """Region face an outward direction points at, or None if not axis aligned (within 1 deg)."""
    dx, dy = direction
    n = math.hypot(dx, dy)
    if n == 0:
        return None
    ang = math.atan2(dy, dx)
    for face, a in (
        ("xmax", 0.0),
        ("ymax", math.pi / 2),
        ("xmin", math.pi),
        ("ymin", -math.pi / 2),
    ):
        d = abs((ang - a + math.pi) % (2 * math.pi) - math.pi)
        if d <= _AXIS_TOL:
            return face
    return None


def _candidates(geo: BoardGeometry, cut: RegionCut, net: str, allow_leaf: bool) -> list[_Cand]:
    out: list[_Cand] = []
    for c in cut.boundary_crossings:
        if c.net != net:
            continue
        # the trace must run perpendicular to the face
        axis_dir = {"xmin": math.pi, "xmax": 0.0, "ymin": -math.pi / 2, "ymax": math.pi / 2}[c.face]
        d = abs((c.direction - axis_dir + math.pi) % (2 * math.pi) - math.pi)
        d = min(d, math.pi - d)  # travel direction may be inward or outward
        if d > _AXIS_TOL:
            continue
        out.append(_Cand(net, c.face, c.layer, c.position, c.width, c.seg_id, False))
    if allow_leaf:
        topo = geo.topology(net)
        deg: dict[str, int] = {}
        for e in topo.edges:
            deg[e.a] = deg.get(e.a, 0) + 1
            deg[e.b] = deg.get(e.b, 0) + 1
        segs = {s.id: s for s in geo.segments(net)}
        xmin, ymin, xmax, ymax = cut.bbox
        for n in topo.nodes:
            if n.kind not in ("pad", "end") or deg.get(n.id, 0) != 1:
                continue
            edge = next(e for e in topo.edges if n.id in (e.a, e.b))
            seg = segs.get(edge.seg_id or "")
            if seg is None or seg.seg.kind != "line":
                continue
            sx, sy = seg.seg.start
            ex, ey = seg.seg.end
            px, py = n.position
            if math.hypot(px - sx, py - sy) <= math.hypot(px - ex, py - ey):
                vec = (sx - ex, sy - ey)
            else:
                vec = (ex - sx, ey - sy)
            face = _axis_face(vec)
            if face is None or not (xmin <= px <= xmax and ymin <= py <= ymax):
                continue
            out.append(_Cand(net, face, seg.layer, (px, py), seg.width, seg.id, True))
    out.sort(key=lambda c: (FACES.index(c.face), c.net, c.seg_id))
    return out


def _poly_shapely(p: Polygon) -> shapely.Polygon:
    return shapely.Polygon(p.exterior.array, [h.array for h in p.holes])


def _rect_poly(x0: float, y0: float, x1: float, y1: float) -> Polygon:
    ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return Polygon(exterior=Points(ring))


def _extend_body(
    st: Structure3D, body_id: str, c: _Cand, bbox: tuple[float, float, float, float]
) -> Structure3D:
    """Add a strip from a free trace end to the region face to the named body."""
    px, py = c.pos
    hw = c.width / 2.0
    face = bbox_face = _face_coord(bbox, c.face)
    if c.face in ("xmin", "xmax"):
        poly = _rect_poly(min(px, bbox_face), py - hw, max(px, bbox_face), py + hw)
    else:
        poly = _rect_poly(px - hw, min(py, face), px + hw, max(py, face))
    bodies = tuple(
        b.model_copy(update={"polygons": (*b.polygons, poly)}) if b.id == body_id else b
        for b in st.conductors
    )
    return st.model_copy(update={"conductors": bodies})


def _line_type(st: Structure3D, c: _Cand, body: ConductorBody) -> str:
    pt = shapely.Point(c.pos)
    zs = 0.5 * (body.z_bottom + body.z_top)
    above = below = False
    for b in st.conductors:
        if b.role != "reference":
            continue
        zb = 0.5 * (b.z_bottom + b.z_top)
        if abs(zb - zs) < 1e-9:
            continue
        if any(_poly_shapely(p).buffer(1e-9).covers(pt) for p in b.polygons):
            above |= zb > zs
            below |= zb < zs
    if above and below:
        return "stripline"
    if above or below:
        return "microstrip"
    return "general"


def _stack_height(st: Structure3D) -> float:
    return st.bbox.zmax - st.bbox.zmin


def default_air_padding(st: Structure3D, f_max: float) -> float:
    lam0 = C0 / f_max
    return float(min(max(lam0 / 10.0, 5.0 * _stack_height(st)), lam0 / 4.0))


def _plane_at_edge(st: Structure3D, top: bool) -> bool:
    """True when the outermost conductor layer at that z end is a reference plane covering
    (nearly) the whole region footprint."""
    if not st.conductors:
        return False

    def zkey(b: ConductorBody) -> float:
        return b.z_top if top else -b.z_bottom

    zedge = max(zkey(b) for b in st.conductors)
    edge = [b for b in st.conductors if abs(zkey(b) - zedge) < 1e-9]
    if any(b.role == "signal" for b in edge):
        return False
    refs = [b for b in edge if b.role == "reference"]
    if not refs:
        return False
    box = st.bbox
    area = (box.xmax - box.xmin) * (box.ymax - box.ymin)
    geom = shapely.union_all([_poly_shapely(p) for b in refs for p in b.polygons])
    return bool(geom.area >= 0.95 * area)


def _is_closed(st: Structure3D) -> bool:
    sig = [b for b in st.conductors if b.role == "signal"]
    if not sig:
        return False
    for s in sig:
        zs = 0.5 * (s.z_bottom + s.z_top)
        zr = [0.5 * (b.z_bottom + b.z_top) for b in st.conductors if b.role == "reference"]
        if not (any(z < zs - 1e-9 for z in zr) and any(z > zs + 1e-9 for z in zr)):
            return False
    return True


def choose_boundaries(
    st: Structure3D, port_faces: set[str], cfg: BoundaryConfig, f_max: float
) -> Boundaries:
    """Wave-port faces absorb; reference planes at the z faces are PEC; side walls are PEC for
    closed (stripline-like) regions and absorbing, with air padding, for open ones."""
    closed = cfg.mode == "closed" or (cfg.mode == "auto" and _is_closed(st))
    bc: dict[str, str] = {}
    for f in FACES:
        bc[f] = "absorbing" if f in port_faces else ("pec" if closed else "absorbing")
    bc["zmin"] = "pec" if _plane_at_edge(st, top=False) else "absorbing"
    bc["zmax"] = "pec" if _plane_at_edge(st, top=True) else "absorbing"
    pad: float | None = None
    if "absorbing" in bc.values():
        pad = cfg.air_padding if cfg.air_padding is not None else default_air_padding(st, f_max)
    return Boundaries(air_padding=pad, **bc)  # type: ignore[arg-type]


def _port_rect(st: Structure3D, face: str, bnd: Boundaries) -> tuple[float, float, float, float]:
    b = st.bbox
    u0, u1 = (b.ymin, b.ymax) if face[0] == "x" else (b.xmin, b.xmax)
    pad = bnd.air_padding or 0.0
    z0 = b.zmin - (pad if bnd.zmin == "absorbing" else 0.0)
    z1 = b.zmax + (pad if bnd.zmax == "absorbing" else 0.0)
    return (u0, u1, z0, z1)


def _auto_ports(cfg: EmSparamsConfig, cands: dict[str, list[_Cand]]) -> list[TraceEndPort]:
    nets = cfg.region.nets[:2]
    faces = sorted(set.intersection(*(({c.face for c in cands[n]}) for n in nets)), key=FACES.index)
    return [
        TraceEndPort(
            name=f"P{i + 1}",
            nets=tuple(nets),
            side=f,  # type: ignore[arg-type]
            modes="differential" if len(nets) == 2 else "single",
        )
        for i, f in enumerate(faces)
    ]


def build_problem(board_geo: BoardGeometry, cfg: EmSparamsConfig) -> Built:
    """Cut the region, resolve the ports and assemble the ``EMProblem``.

    Raises ``SetupError`` (with diagnostics) when a port cannot be placed.
    """
    reg = cfg.region
    diags: list[Diagnostic] = []
    spec = RegionSpec(
        nets=reg.nets,
        reference_nets=reg.reference_nets,
        bbox=reg.bbox,
        margin=reg.margin,
        keep_all_copper=reg.keep_all_copper,
        layers=reg.layers,
        f_max=cfg.frequency.f_max,
    )
    cut = board_geo.cut_region(spec)
    st = cut.structure
    allow_leaf = reg.bbox is None
    cands = {n: _candidates(board_geo, cut, n, allow_leaf) for n in reg.nets}
    setup_ports: list[Any] = list(cfg.ports)
    if not setup_ports:
        setup_ports = _auto_ports(cfg, cands) if all(cands[n] for n in reg.nets[:2]) else []
        if not setup_ports:
            raise SetupError(
                "no ports given and no trace reaches the region boundary",
                [
                    _err(
                        "EM.NO_PORTS",
                        "no ports given and the nets do not cross the region boundary",
                        "Add ports to the config, or cut the region so the trace crosses it.",
                    )
                ],
            )

    # ---- wave ports from trace ends ------------------------------------------------------
    plan: list[tuple[TraceEndPort, list[_Cand]]] = []
    used: dict[tuple[str, str], bool] = {}
    for p in setup_ports:
        if not isinstance(p, TraceEndPort):
            continue
        chosen: list[_Cand] = []
        faces = [p.side] if p.side != "auto" else list(FACES)
        for face in faces:
            picks = []
            for n in p.nets:
                cs = [c for c in cands.get(n, []) if c.face == face and not used.get((n, face))]
                picks.append(cs[0] if cs else None)
            if all(picks):
                chosen = [c for c in picks if c is not None]
                break
        if not chosen:
            raise SetupError(
                f"port {p.name!r}: nets {list(p.nets)} do not reach face {p.side!r}",
                [
                    _err(
                        "EM.PORT_NOT_ON_FACE",
                        f"port {p.name}: no trace of {list(p.nets)} crosses the region "
                        f"face {p.side}",
                        "Choose a region bbox that the trace crosses perpendicularly, or leave "
                        "the bbox automatic so free trace ends are extended to the faces.",
                        item=p.name,
                    )
                ],
            )
        for c in chosen:
            used[(c.net, c.face)] = True
        plan.append((p, chosen))

    bodies = {b.id: b for b in st.conductors}
    wave: list[tuple[PortInfo, _Cand, ConductorBody, TraceEndPort]] = []
    for p, chosen in plan:
        diff = len(p.nets) == 2
        for k, c in enumerate(chosen):
            bid = f"{c.layer}:{c.net}"
            if bid not in bodies:
                raise SetupError(
                    f"port {p.name!r}: no conductor body {bid!r} in the region",
                    [_err("EM.PORT_NO_BODY", f"port {p.name}: conductor {bid} missing", "")],
                )
            if c.leaf:
                st = _extend_body(st, bid, c, cut.bbox)
                bodies = {b.id: b for b in st.conductors}
                diags.append(
                    Diagnostic(
                        severity="info",
                        code="EM.PORT_EXTENDED",
                        message=f"port {p.name}: trace of {c.net} extended from its free end "
                        f"to region face {c.face}",
                        item=p.name,
                        hint="Give an explicit region bbox crossing the trace to avoid this.",
                    )
                )
            leg = ("P", "N")[k] if diff else None
            info = PortInfo(
                em_name=f"{p.name}_{leg}" if leg else p.name,
                setup_name=p.name,
                kind="wave",
                nets=(c.net,),
                leg=leg,
                face=c.face,
                layer=c.layer,
                position=c.pos,
                seg_id=c.seg_id,
                extended=c.leaf,
                z0_cfg=p.z0,
                modes=p.modes,
            )
            wave.append((info, c, bodies[bid], p))
    port_faces = {c.face for _i, c, _b, _p in wave}
    bnd = choose_boundaries(st, port_faces, cfg.boundary, cfg.frequency.f_max)

    ems: dict[str, EMPort] = {}
    for info, c, body, p in wave:
        if info.leg == "P":
            diags.append(
                Diagnostic(
                    severity="warning",
                    code="EM.DIFF_LEG_PORTS",
                    message=f"port {p.name}: differential pair modelled as two single-ended "
                    "TL ports (the coupled mode is recovered by mixed-mode conversion)",
                    item=p.name,
                    hint="Use a modal wave-port adapter (Palace) for strongly coupled pairs.",
                )
            )
        ems[info.em_name] = EMWavePort(
            name=info.em_name,
            face=c.face,  # type: ignore[arg-type]
            rect=_port_rect(st, c.face, bnd),
            n_modes=1,
            line_type=_line_type(st, c, body),  # type: ignore[arg-type]
            signal_conductors=(body.id,),
            z0=p.z0,
            deembed=p.deembed,
            excite=True,
        )
    # keep setup order (differential legs P, N adjacent)
    order: list[PortInfo] = []
    em_ports: list[EMPort] = []
    wave_by_setup: dict[str, list[PortInfo]] = {}
    for info, *_ in wave:
        wave_by_setup.setdefault(info.setup_name, []).append(info)

    for p in setup_ports:
        if isinstance(p, TraceEndPort):
            for info in wave_by_setup.get(p.name, []):
                order.append(info)
                em_ports.append(ems[info.em_name])
        elif isinstance(p, LumpedPort):
            pos = board_geo.resolve_terminal(p.positive)
            neg = board_geo.resolve_terminal(p.negative, near=pos.position, layer=pos.layer)
            width = max(pos.extent[0], pos.extent[1]) or 0.2e-3
            em_ports.append(
                EMLumpedPort(
                    name=p.name,
                    positive=pos.to_em(),
                    negative=neg.to_em(),
                    width=float(width),
                    z0=p.z0,
                )
            )
            order.append(
                PortInfo(
                    p.name,
                    p.name,
                    "lumped",
                    nets=tuple(n for n in (pos.net, neg.net) if n),
                    position=pos.position,
                    layer=pos.layer,
                    z0_cfg=p.z0,
                )
            )
        elif isinstance(p, CoaxPort):
            pad = board_geo.resolve_terminal(p.pad)
            top = pad.z >= 0.5 * (st.bbox.zmin + st.bbox.zmax)
            r_in = p.inner_radius if p.inner_radius is not None else 0.5 * min(pad.extent) / 2
            em_ports.append(
                EMCoaxPort(
                    name=p.name,
                    center=pad.position,
                    z=st.bbox.zmax if top else st.bbox.zmin,
                    r_inner=max(r_in, 1e-6),
                    r_outer=p.outer_radius,
                    dielectric=p.dielectric,
                    direction="-z" if top else "+z",
                    z0=p.z0,
                )
            )
            order.append(
                PortInfo(
                    p.name, p.name, "coax", position=pad.position, layer=pad.layer, z0_cfg=p.z0
                )
            )
        else:
            diags.append(
                Diagnostic(
                    severity="error",
                    code="EM.PORT_KIND",
                    message=f"port {p.name}: kind {p.kind!r} is not an EM port",
                    hint="Use trace_end, lumped or coax ports for em.sparams.",
                    item=p.name,
                )
            )
    if any(d.severity == "error" for d in diags):
        raise SetupError("invalid ports", diags)

    pairs: list[tuple[int, int]] = []
    for i, inf in enumerate(order[:-1]):
        if inf.leg == "P" and order[i + 1].leg == "N" and order[i + 1].setup_name == inf.setup_name:
            pairs.append((i, i + 1))
    want_mm = (
        bool(pairs)
        and len(pairs) * 2 == len(order)
        and any(o.modes in ("differential", "both") for o in order)
    )

    mesh = cfg.mesh
    expert = dict(mesh.expert)
    if any(isinstance(p, EMWavePort) for p in em_ports):
        for k, v in _WAVE_PORT_DEFAULTS.get(cfg.solver, {}).items():
            expert.setdefault(k, v)
    expert.update(cfg.adapter_options)
    if expert != mesh.expert:
        mesh = mesh.model_copy(update={"expert": expert})
    freq = cfg.frequency
    frac = _FMIN_FRACTION.get(cfg.solver, 0.0) if cfg.raise_fmin else 0.0
    if frac and freq.f_min < frac * freq.f_max:
        freq = freq.model_copy(update={"f_min": frac * freq.f_max})
        diags.append(
            Diagnostic(
                severity="info",
                code="EM.FMIN_RAISED",
                message=f"sweep starts at {freq.f_min / 1e9:.3g} GHz: the {cfg.solver} excitation "
                "has too little energy below it; DC is extrapolated by the quality gate",
                hint="Set raise_fmin=false to sweep from the requested f_min anyway.",
            )
        )
    problem = EMProblem(
        name="em_sparams",
        structure=st,
        ports=tuple(em_ports),
        boundaries=bnd,
        frequency=freq,
        mesh=mesh,
        convergence=cfg.convergence,
    )
    return Built(problem, order, cut, diags, tuple(pairs), want_mm)
