# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="arg-type,assignment,attr-defined,call-overload,index,return-value"
# mypy: disable-error-code="no-any-return,unused-ignore,import-untyped,union-attr,var-annotated"
"""Solver-neutral DC network: copper sheets, via barrels, terminal pin groups.

``build_network`` turns a board plus a ``DcConfig`` into a :class:`DcNetwork` without meshing.
Both the FEM solver (``oa_pi.dc.fem``) and the raster reference (``oa_pi.reference.raster``)
consume it, so the cross-check compares discretizations of one physical problem.

Contact modelling (research 04 section 1.1):

- A via/plated-pad barrel is one lumped resistor per pair of consecutive copper layers it
  touches, ``R = rho L / (pi (ro^2 - ri^2))``, ``ro`` = drill radius, ``ri = ro - plating``.
  Both ends are tied to *every* node of a ring of radius ``ro`` (the annulus of the pad/plane
  copper starts at the hole wall), so there is no point-contact singularity.
- Thermal reliefs and antipads are already in the zone fill (``ZoneFill`` is actual copper) and
  are meshed as geometry.
- A component pin is an ideal equipotential conductor over its pad polygon: the pad is removed
  from the meshed copper (its outline becomes a mesh boundary whose nodes are tied to the pin),
  and a barrel whose centre lies in the pad connects to the pin directly. The pins of a load or
  regulator group are merged into one node (exact equipotential constraint).
- Zones with ``fill_state == "unfilled"`` (or no fill at all) on the rail/return nets are refused;
  ``stale_suspected`` is a warning.
- Copper islands without at least two attachments (vias, pins) carry no current and are dropped;
  galvanic classes with a load but no regulator are an error; classes with neither are reported.
"""

from __future__ import annotations

import itertools
import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
import shapely
from shapely import Polygon as ShPolygon

from oa_geometry import BoardGeometry
from oa_geometry.vias import pad_copper_layers, via_copper_layers
from oa_model.board import Board
from oa_model.common import Diagnostic
from oa_model.index import ItemRef
from oa_model.materials import ConductorMaterial
from oa_model.pads import Pad
from oa_model.stackup import ConductorLayer
from oa_physics import resistivity
from oa_pi.dc.config import DcConfig, LoadConfig
from oa_pi.errors import PiFloatingError, PiGeometryError, PiSetupError, PiUnfilledZoneError

__all__ = [
    "Barrel",
    "DcNetwork",
    "LoadSpec",
    "Pin",
    "Sheet",
    "TemperatureField",
    "build_network",
    "check_setup",
]

TemperatureField = float | Callable[[str, npt.NDArray[np.float64]], npt.NDArray[np.float64]]
"""Uniform copper temperature [K], or ``f(layer_id, xy (n, 2)) -> T (n,)`` [K]."""

_TOL = 1e-9


@dataclass
class Sheet:
    """Kept copper of one (layer, net): a 2D conductor of constant thickness."""

    key: str  # "<layer>:<net>", also the mesh piece id
    layer: str
    net: str
    z: float
    thickness: float
    material: ConductorMaterial
    polygons: list[ShPolygon]


@dataclass
class Barrel:
    """A via or plated-through pad: lumped resistors between consecutive layers."""

    id: str
    net: str
    xy: tuple[float, float]
    r_out: float
    layers: tuple[str, ...]  # retained layers, top to bottom (>= 2)
    seg_r: tuple[float, ...]  # resistance of each consecutive pair [ohm]
    seg_len: tuple[float, ...]
    area: float  # conducting copper cross-section [m^2]
    kind: str  # "via" | "pad"
    pin_ends: dict[str, str] = field(default_factory=dict)  # layer -> pin id the end sits on


@dataclass
class Pin:
    """A component pin: the pad polygon on the component-side copper layer."""

    id: str  # pad id
    refdes: str
    number: str
    net: str
    layer: str
    region: ShPolygon
    xy: tuple[float, float]


@dataclass
class LoadSpec:
    name: str
    cfg: LoadConfig
    rail_pins: list[Pin]
    ret_pins: list[Pin]


@dataclass
class DcNetwork:
    cfg: DcConfig
    sheets: list[Sheet]
    barrels: list[Barrel]
    vrm_p: list[Pin]
    vrm_n: list[Pin]
    sense_p: list[Pin]
    sense_n: list[Pin]
    loads: list[LoadSpec]
    temperature: TemperatureField
    diagnostics: list[Diagnostic] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def rail(self) -> str:
        return self.cfg.rail.net

    @property
    def ret(self) -> str | None:
        return self.cfg.rail.return_

    def sheet(self, key: str) -> Sheet:
        for s in self.sheets:
            if s.key == key:
                return s
        raise KeyError(key)

    def all_pins(self) -> list[Pin]:
        out = [*self.vrm_p, *self.vrm_n, *self.sense_p, *self.sense_n]
        for ld in self.loads:
            out += [*ld.rail_pins, *ld.ret_pins]
        return out


# -------------------------------------------------------------------------------------------
# helpers
# -------------------------------------------------------------------------------------------


def _diag(sev: str, code: str, msg: str, **kw: Any) -> Diagnostic:
    return Diagnostic(severity=sev, code=code, message=msg, **kw)


def _temp_at(t: TemperatureField, layer: str, xy: tuple[float, float]) -> float:
    if callable(t):
        return float(t(layer, np.array([xy], dtype=np.float64))[0])
    return float(t)


def _pad_layer(board: Board, geo: BoardGeometry, pad: Pad) -> str:
    """Component-side copper layer of a pad."""
    layers = pad_copper_layers(geo.idx.padstack(pad.padstack))
    if not layers:
        raise PiGeometryError(f"pad {pad.id} has no copper", path=pad.id)
    order = sorted(layers, key=lambda s: int(s[1:]))
    side = "top"
    if pad.component is not None:
        try:
            side = geo.idx.component(pad.component).side
        except KeyError:
            side = "top"
    return order[0] if side == "top" else order[-1]


def _pad_region(geo: BoardGeometry, pad: Pad, layer: str) -> ShPolygon:
    idx = geo.strtree(layer)
    pt = shapely.Point(pad.position)
    ref = ItemRef("pad", pad.id)
    for i in idx.query_indices(pt):
        if idx.ref(int(i)) == ref:
            g = idx.geometry(int(i))
            if isinstance(g, ShPolygon):
                return g
            parts = [p for p in getattr(g, "geoms", []) if isinstance(p, ShPolygon)]
            if parts:
                return max(parts, key=lambda p: p.area)
    raise PiGeometryError(
        f"pad {pad.id} has no copper on {layer}", path=pad.id, hint="Check the padstack layers."
    )


def _resolve_pin(board: Board, geo: BoardGeometry, ref: str, path: str) -> Pad:
    comp, _, num = ref.partition(".")
    for p in board.pads:
        if p.component == comp and p.number == num:
            return p
    raise PiSetupError(
        f"no pin {ref!r} on the board",
        path=path,
        hint="Use 'REFDES.PIN', e.g. 'U1.AA12', of an existing pad.",
    )


def _group_pins(
    board: Board,
    geo: BoardGeometry,
    component: str,
    net: str,
    numbers: tuple[str, ...],
    path: str,
) -> list[Pin]:
    if not any(c.refdes == component for c in board.components):
        raise PiSetupError(
            f"component {component!r} is not on the board",
            path=path,
            hint="Check the refdes spelling.",
        )
    pads = [
        p
        for p in board.pads
        if p.component == component and p.net == net and (not numbers or p.number in numbers)
    ]
    if numbers:
        missing = sorted(set(numbers) - {p.number for p in pads})
        if missing:
            raise PiSetupError(
                f"{component} pins {missing} are not on net {net!r}",
                path=path,
                hint="List only pins that sit on the rail/return net.",
            )
    if not pads:
        raise PiSetupError(
            f"{component} has no pins on net {net!r}",
            path=path,
            hint="Check the component refdes and the net name.",
        )
    return [_make_pin(geo, board, p) for p in sorted(pads, key=lambda p: p.id)]


def _make_pin(geo: BoardGeometry, board: Board, pad: Pad) -> Pin:
    layer = _pad_layer(board, geo, pad)
    region = _pad_region(geo, pad, layer)
    return Pin(
        id=pad.id,
        refdes=pad.component or "",
        number=pad.number,
        net=pad.net or "",
        layer=layer,
        region=region,
        xy=(float(region.centroid.x), float(region.centroid.y)),
    )


def _conductor_material(board: Board, layer: ConductorLayer) -> ConductorMaterial:
    for m in board.stackup.materials:
        if isinstance(m, ConductorMaterial) and m.name == layer.material:
            return m
    return ConductorMaterial(name=layer.material)


# -------------------------------------------------------------------------------------------
# refusals
# -------------------------------------------------------------------------------------------


def check_zones(board: Board, nets: tuple[str, ...]) -> list[Diagnostic]:
    """Raise ``PiUnfilledZoneError`` for unfilled zones on ``nets``; warn on stale fills."""
    out: list[Diagnostic] = []
    for z in board.zones:
        if z.net not in nets or z.keepout is not None:
            continue
        where = f"/zones/{z.id}"
        if z.fill_state == "unfilled" or not z.fills:
            raise PiUnfilledZoneError(
                f"zone {z.id} on net {z.net!r} has no filled copper",
                path=where,
                hint="Refill all zones in the layout tool (KiCad: Edit > Fill All Zones, B) "
                "and re-export the board.",
            )
        if z.fill_state == "stale_suspected":
            out.append(
                _diag(
                    "warning",
                    "PI.ZONE_STALE",
                    f"zone {z.id} on net {z.net!r} may have a stale fill; the IR drop uses the "
                    "stored copper",
                    item=z.id,
                    path=where,
                    hint="Refill all zones and re-run to be sure the copper is current.",
                )
            )
    return out


def check_setup(board: Board, cfg: DcConfig) -> list[Diagnostic]:
    """Cheap setup checks without geometry (nets, components, pins, zone fills).

    Raises ``Pi*Error`` on the first problem; returns the warnings otherwise.
    """
    rail, ret = cfg.rail.net, cfg.rail.return_
    nets = (rail,) if ret is None else (rail, ret)
    names = {n.id for n in board.nets}
    for k, n in enumerate(nets):
        if n not in names:
            raise PiSetupError(
                f"net {n!r} is not on the board",
                path="/config/rail" + ("/net" if k == 0 else "/return"),
                hint="Use an existing net name; check the case and any '+' or '/' characters.",
            )
    comps = {c.refdes for c in board.components}

    def need(component: str, net: str, numbers: tuple[str, ...], path: str) -> None:
        if component not in comps:
            raise PiSetupError(
                f"component {component!r} is not on the board",
                path=path,
                hint="Check the refdes spelling.",
            )
        have = {p.number for p in board.pads if p.component == component and p.net == net}
        if not have or set(numbers) - have:
            miss = sorted(set(numbers) - have) if numbers else []
            raise PiSetupError(
                f"{component} has no pins {miss or ''} on net {net!r}".replace("  ", " "),
                path=path,
                hint="List only pins that sit on the rail/return net.",
            )

    need(cfg.vrm.component, rail, cfg.vrm.pins, "/config/vrm/pins")
    if ret is not None:
        need(cfg.vrm.component, ret, cfg.vrm.return_pins, "/config/vrm/return_pins")
    for k, ld in enumerate(cfg.loads):
        if ld.nets and ld.nets[0] != rail:
            raise PiSetupError(
                f"load {ld.component} draws from {ld.nets[0]!r}, not the rail {rail!r}",
                path=f"/config/loads/{k}/nets",
                hint="List the rail net first in 'nets'.",
            )
        need(ld.component, rail, ld.pins, f"/config/loads/{k}/pins")
        if ret is not None:
            need(ld.component, ret, ld.return_pins, f"/config/loads/{k}/return_pins")
    if cfg.vrm.sense is not None:
        for ref, path in ((cfg.vrm.sense.pos, "pos"), (cfg.vrm.sense.neg, "neg")):
            if ref is not None:
                comp, _, num = ref.partition(".")
                if not any(p.component == comp and p.number == num for p in board.pads):
                    raise PiSetupError(
                        f"no pin {ref!r} on the board",
                        path=f"/config/vrm/sense/{path}",
                        hint="Use 'REFDES.PIN', e.g. 'U1.AA12', of an existing pad.",
                    )
    return check_zones(board, nets)


# -------------------------------------------------------------------------------------------
# barrels
# -------------------------------------------------------------------------------------------


def _barrel_resistances(
    board: Board,
    geo: BoardGeometry,
    layers: tuple[str, ...],
    r_out: float,
    plating: float,
    filled: bool,
    temperature: TemperatureField,
    xy: tuple[float, float],
) -> tuple[tuple[float, ...], tuple[float, ...], float]:
    """Segment resistances, lengths and copper area of a barrel through retained ``layers``."""
    cl = [geo.conductor(lid) for lid in layers]
    ri = 0.0 if filled else max(r_out - plating, 0.0)
    area = math.pi * (r_out**2 - ri**2)
    mat = _conductor_material(board, cl[0])
    rho = float(resistivity(mat, _temp_at(temperature, layers[0], xy)))
    lens = []
    for k in range(len(cl) - 1):
        a, b = cl[k], cl[k + 1]
        ln = (a.z_bottom + a.z_top) / 2.0 - (b.z_bottom + b.z_top) / 2.0
        if k == 0:
            ln += a.thickness / 2.0
        if k == len(cl) - 2:
            ln += b.thickness / 2.0
        lens.append(ln)
    return tuple(rho * ln / area for ln in lens), tuple(lens), area


@dataclass
class _Cand:
    """A barrel candidate before pruning."""

    id: str
    net: str
    xy: tuple[float, float]
    r_out: float
    plating: float
    filled: bool
    layers: tuple[str, ...]
    kind: str


def _candidates(board: Board, geo: BoardGeometry, nets: tuple[str, ...]) -> list[_Cand]:
    out: list[_Cand] = []
    for v in board.vias:
        if v.net not in nets:
            continue
        ps = geo.idx.padstack(v.padstack)
        if ps.drill is None:
            continue
        lays = via_copper_layers(geo.idx, v, ps)
        if len(lays) < 2:
            continue
        out.append(
            _Cand(
                v.id,
                v.net or "",
                v.position,
                ps.drill.diameter / 2.0,
                ps.drill.plating_thickness,
                v.protection.filled == "conductive",
                lays,
                "via",
            )
        )
    for p in board.pads:
        if p.net not in nets:
            continue
        ps = geo.idx.padstack(p.padstack)
        if ps.drill is None or ps.drill.kind != "plated":
            continue
        lays = tuple(sorted(pad_copper_layers(ps), key=lambda s: int(s[1:])))
        if len(lays) < 2:
            continue
        out.append(
            _Cand(
                p.id,
                p.net or "",
                p.position,
                ps.drill.diameter / 2.0,
                ps.drill.plating_thickness,
                False,
                lays,
                "pad",
            )
        )
    return out


# -------------------------------------------------------------------------------------------
# main entry
# -------------------------------------------------------------------------------------------


def build_network(
    board: Board,
    geo: BoardGeometry,
    cfg: DcConfig,
    temperature: TemperatureField | None = None,
) -> DcNetwork:
    """Extract the solvable network. Raises ``Pi*Error`` (code/path/hint) on refusals."""
    temp: TemperatureField = cfg.temperature if temperature is None else temperature
    rail, ret = cfg.rail.net, cfg.rail.return_
    nets = (rail,) if ret is None else (rail, ret)
    names = {n.id for n in board.nets}
    for k, n in enumerate(nets):
        if n not in names:
            raise PiSetupError(
                f"net {n!r} is not on the board",
                path="/config/rail" + ("/net" if k == 0 else "/return"),
                hint="Use an existing net name; check the case and any '+' or '/' characters.",
            )
    diags = check_zones(board, nets)

    # ---- pins ------------------------------------------------------------------------------
    vrm = cfg.vrm
    vrm_p = _group_pins(board, geo, vrm.component, rail, vrm.pins, "/config/vrm/pins")
    vrm_n = (
        _group_pins(board, geo, vrm.component, ret, vrm.return_pins, "/config/vrm/return_pins")
        if ret is not None
        else []
    )
    sense_p: list[Pin] = []
    sense_n: list[Pin] = []
    if vrm.sense is not None:
        pad = _resolve_pin(board, geo, vrm.sense.pos, "/config/vrm/sense/pos")
        if pad.net != rail:
            raise PiSetupError(
                f"sense pin {vrm.sense.pos} is on net {pad.net!r}, not {rail!r}",
                path="/config/vrm/sense/pos",
                hint="Sense+ must sit on the rail net.",
            )
        sense_p = [_make_pin(geo, board, pad)]
        if vrm.sense.neg is not None:
            padn = _resolve_pin(board, geo, vrm.sense.neg, "/config/vrm/sense/neg")
            if ret is None or padn.net != ret:
                raise PiSetupError(
                    f"sense pin {vrm.sense.neg} is on net {padn.net!r}, not the return net",
                    path="/config/vrm/sense/neg",
                    hint="Sense- must sit on the return net.",
                )
            sense_n = [_make_pin(geo, board, padn)]
    loads: list[LoadSpec] = []
    for k, lc in enumerate(cfg.loads):
        if lc.nets and lc.nets[0] != rail:
            raise PiSetupError(
                f"load {lc.component} draws from {lc.nets[0]!r}, not the rail {rail!r}",
                path=f"/config/loads/{k}/nets",
                hint="List the rail net first in 'nets'.",
            )
        rp = _group_pins(board, geo, lc.component, rail, lc.pins, f"/config/loads/{k}/pins")
        cp = (
            _group_pins(
                board, geo, lc.component, ret, lc.return_pins, f"/config/loads/{k}/return_pins"
            )
            if ret is not None
            else []
        )
        loads.append(LoadSpec(lc.name or lc.component, lc, rp, cp))
    names_seen = [ld.name for ld in loads]
    if len(set(names_seen)) != len(names_seen):
        raise PiSetupError(
            "load names must be unique",
            path="/config/loads",
            hint="Set 'name' on loads that share a component.",
        )

    # ---- copper components per (layer, net) --------------------------------------------------
    comps: dict[tuple[str, str], list[ShPolygon]] = {}
    for lid in geo.layer_ids():
        for n in nets:
            mp = geo.copper(lid, n)
            if not mp.is_empty:
                comps[(lid, n)] = sorted(mp.geoms, key=lambda p: (p.bounds, p.area))

    def comp_of(layer: str, net: str, pt: tuple[float, float]) -> int | None:
        polys = comps.get((layer, net))
        if not polys:
            return None
        p = shapely.Point(pt)
        for i, poly in enumerate(polys):
            if poly.distance(p) <= _TOL:
                return i
        return None

    cands = _candidates(board, geo, nets)
    # barrel -> {layer: comp index} on layers where its net has copper under the barrel
    bar_comp: dict[str, dict[str, int]] = {}
    for c in cands:
        m: dict[str, int] = {}
        for lid in c.layers:
            i = comp_of(lid, c.net, c.xy)
            if i is not None:
                m[lid] = i
        bar_comp[c.id] = m
    pin_comp: dict[str, int | None] = {}
    for pn in (
        *vrm_p,
        *vrm_n,
        *sense_p,
        *sense_n,
        *[q for ld in loads for q in (*ld.rail_pins, *ld.ret_pins)],
    ):
        i = comp_of(pn.layer, pn.net, pn.region.representative_point().coords[0])
        pin_comp[pn.id] = i

    # ---- prune dead-end components to a fixpoint ----------------------------------------------
    active_bar = {c.id for c in cands if len(bar_comp[c.id]) >= 2}
    while True:
        count: dict[tuple[str, str, int], int] = defaultdict(int)
        for c in cands:
            if c.id in active_bar:
                for lid, i in bar_comp[c.id].items():
                    count[(lid, c.net, i)] += 1
        for pn in {
            p.id: p
            for p in (
                *vrm_p,
                *vrm_n,
                *sense_p,
                *sense_n,
                *[q for ld in loads for q in (*ld.rail_pins, *ld.ret_pins)],
            )
        }.values():
            i = pin_comp[pn.id]
            if i is not None:
                count[(pn.layer, pn.net, i)] += 1
        kept = {k for k, n in count.items() if n >= 2}
        new_active = {
            c.id
            for c in cands
            if c.id in active_bar
            and sum(1 for lid, i in bar_comp[c.id].items() if (lid, c.net, i) in kept) >= 2
        }
        if new_active == active_bar:
            break
        active_bar = new_active

    # ---- galvanic classes (union-find over kept components) ------------------------------------
    parent: dict[Any, Any] = {}

    def find(a: Any) -> Any:
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: Any, b: Any) -> None:
        parent[find(a)] = find(b)

    for k in kept:
        find(k)
    final_layers: dict[str, tuple[str, ...]] = {}
    for c in cands:
        if c.id not in active_bar:
            continue
        lays = tuple(lid for lid in c.layers if (lid, c.net, bar_comp[c.id].get(lid, -1)) in kept)
        final_layers[c.id] = lays
        for a, b in itertools.pairwise(lays):
            union((a, c.net, bar_comp[c.id][a]), (b, c.net, bar_comp[c.id][b]))

    def pin_key(pn: Pin) -> tuple[str, str, int] | None:
        i = pin_comp[pn.id]
        k = (pn.layer, pn.net, i) if i is not None else None
        return k if k in kept else None

    def group_class(pins: list[Pin]) -> set[Any]:
        return {find(k) for p in pins if (k := pin_key(p)) is not None}

    # merge pins of one group into one class (they are shorted)
    groups: list[list[Pin]] = [vrm_p, vrm_n]
    for ld in loads:
        if ld.cfg.model == "pin_group":
            groups += [ld.rail_pins, ld.ret_pins]
    for g in groups:
        ks = [pin_key(p) for p in g]
        ks2 = [k for k in ks if k is not None]
        for a, b in itertools.pairwise(ks2):
            union(a, b)

    def need_path(pins: list[Pin], what: str, path: str) -> None:
        if pins and not group_class(pins):
            raise PiFloatingError(
                f"{what}: none of its pins connects to the {pins[0].net!r} copper network",
                path=path,
                hint="The pins are not tied to a plane or trace shared with a via/another pin; "
                "check the net assignment and that zones are filled.",
            )

    need_path(vrm_p, f"regulator {vrm.component}", "/config/vrm")
    need_path(vrm_n, f"regulator {vrm.component} return", "/config/vrm/return_pins")
    for k, ld in enumerate(loads):
        need_path(ld.rail_pins, f"load {ld.name}", f"/config/loads/{k}")
        need_path(ld.ret_pins, f"load {ld.name} return", f"/config/loads/{k}/return_pins")
    for pn in sense_p + sense_n:
        need_path([pn], f"sense pin {pn.refdes}.{pn.number}", "/config/vrm/sense")
    # a load must reach a regulator terminal of its side
    src_p, src_n = group_class(vrm_p), group_class(vrm_n)
    for k, ld in enumerate(loads):
        if not (group_class(ld.rail_pins) & src_p):
            raise PiFloatingError(
                f"load {ld.name} is not galvanically connected to regulator {vrm.component}",
                path=f"/config/loads/{k}",
                hint="Open copper between the regulator and the load; check zone fills, "
                "thermal reliefs and via connections.",
            )
        if ret is not None and not (group_class(ld.ret_pins) & src_n):
            raise PiFloatingError(
                f"load {ld.name} return is not connected to the regulator return",
                path=f"/config/loads/{k}/return_pins",
                hint="Check the ground copper between the load and the regulator.",
            )
    live = src_p | src_n
    floating = sorted({str(find(k)) for k in kept if find(k) not in live})
    if floating:
        diags.append(
            _diag(
                "warning",
                "PI.FLOATING_ISLAND",
                f"{len(floating)} copper group(s) are not connected to the regulator and carry "
                "no current; they are left out of the solve",
                hint="Floating copper does not affect IR drop; remove it or tie it to the rail.",
            )
        )

    # ---- assemble sheets & barrels --------------------------------------------------------------
    live_keys = {k for k in kept if find(k) in live}
    sheets: list[Sheet] = []
    for (lid, n), polys in sorted(comps.items(), key=lambda t: (int(t[0][0][1:]), t[0][1])):
        sel = [p for i, p in enumerate(polys) if (lid, n, i) in live_keys]
        if not sel:
            continue
        cl = geo.conductor(lid)
        sheets.append(
            Sheet(
                key=f"{lid}:{n}",
                layer=lid,
                net=n,
                z=(cl.z_bottom + cl.z_top) / 2.0,
                thickness=cl.thickness,
                material=_conductor_material(board, cl),
                polygons=sel,
            )
        )
    barrels: list[Barrel] = []
    n_pruned = 0
    for c in cands:
        lays = tuple(
            lid
            for lid in final_layers.get(c.id, ())
            if (lid, c.net, bar_comp[c.id][lid]) in live_keys
        )
        if len(lays) < 2:
            n_pruned += 1
            continue
        sr, sl, area = _barrel_resistances(
            board, geo, lays, c.r_out, c.plating, c.filled, temp, c.xy
        )
        barrels.append(Barrel(c.id, c.net, c.xy, c.r_out, lays, sr, sl, area, c.kind))
    # a barrel whose centre lies in a pad of a pin ends on that pin on the pad's layer
    pins_by_id = {p.id: p for p in network_pins(vrm_p, vrm_n, sense_p, sense_n, loads)}
    for b in barrels:
        for pn in pins_by_id.values():
            if (
                pn.layer in b.layers
                and pn.net == b.net
                and pn.region.buffer(_TOL).contains(shapely.Point(b.xy))
            ):
                b.pin_ends[pn.layer] = pn.id
    net = DcNetwork(
        cfg=cfg,
        sheets=sheets,
        barrels=barrels,
        vrm_p=vrm_p,
        vrm_n=vrm_n,
        sense_p=sense_p,
        sense_n=sense_n,
        loads=loads,
        temperature=temp,
        diagnostics=diags,
    )
    net.stats = {
        "n_sheets": len(sheets),
        "n_polygons": sum(len(s.polygons) for s in sheets),
        "n_barrels": len(barrels),
        "n_pruned_barrels": n_pruned,
        "n_floating_groups": len(floating),
    }
    return net


def network_pins(
    vrm_p: list[Pin],
    vrm_n: list[Pin],
    sense_p: list[Pin],
    sense_n: list[Pin],
    loads: list[LoadSpec],
) -> list[Pin]:
    out = [*vrm_p, *vrm_n, *sense_p, *sense_n]
    for ld in loads:
        out += [*ld.rail_pins, *ld.ret_pins]
    return out
