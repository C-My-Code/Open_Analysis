# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""IPC-2581 rev B/C reader producing an ``oa_model.Board``.

Mapping notes (see the package README for the fidelity table):

- Coordinates are taken as written (IPC-2581 is already Y-up) and converted to metres.
- Copper comes from ``LayerFeature`` sets: ``Line``/``Arc``/``Polyline`` with a net are tracks,
  filled ``Contour`` geometry with a net is a zone fill (IPC-2581 has no zone object, so the zone
  outline is approximated by the convex hull of its fill), everything else is a ``CopperShape``.
- ``Pad`` elements with a ``PinRef`` are component pads, pads without one that sit on a drilled
  ``VIA`` hole are via pads, other pads are free pads.
- Drill ``Hole`` elements with ``platingStatus="VIA"`` become vias, spanning the layer ``Span``.
- Layers whose function mentions BACKDRILL are mapped to ``Backdrill`` records (best effort).
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from lxml import etree
from shapely.geometry import LineString, MultiPoint
from shapely.geometry import Polygon as ShPolygon
from shapely.geometry.base import BaseGeometry

from oa_ingest_ipc2581._geom import (
    Vertex,
    circle_geom,
    fnum,
    local,
    pad_shape_from_geom,
    path_points,
    polygon_prim,
    ring_polygon,
    standard_shape,
    stroke,
    to_model_polygons,
    union,
    xform_geom,
)
from oa_model.board import Board, BoardMeta, BoardOutline, Component
from oa_model.common import Diagnostic, SourceRef
from oa_model.copper import CopperShape, Track, Zone, ZoneFill
from oa_model.errors import IngestError
from oa_model.geometry import ArcSeg, LineSeg, PathSeg, Polygon
from oa_model.materials import (
    ConductorMaterial,
    ConstantDkDf,
    DielectricMaterial,
    Material,
)
from oa_model.nets import DiffPair, Net
from oa_model.pads import Pad
from oa_model.padstack import (
    Drill,
    PadShape,
    Padstack,
    PadstackLayer,
    PolygonShape,
)
from oa_model.setup import ReadOptions
from oa_model.stackup import ConductorLayer, DielectricLayer, MaskLayer, Stackup
from oa_model.vias import Backdrill, Via

READER_NAME = "oa-ingest-ipc2581 0.1.0"
_UNITS = {
    "MILLIMETER": 1e-3,
    "MICRON": 1e-6,
    "INCH": 0.0254,
    "MIL": 25.4e-6,
    "CENTIMETER": 1e-2,
}
_COND_FUNCS = {"CONDUCTOR", "SIGNAL", "PLANE", "MIXED", "CONDFILM", "CONDFOIL"}
_GROUND_RE = re.compile(r"(^|[/_\-.])(A|D|P|S|E|SIG|PWR|CHASSIS)?(GND|VSS|GROUND|AGND|DGND)", re.I)
_POWER_RE = re.compile(r"^[/+\-]?(\d+V\d*|V(CC|DD|BAT|BUS|IN|OUT|REF)\w*|VPP|VBAT)", re.I)
_PAIR_SUFFIXES = (("_P", "_N"), ("P", "N"), ("+", "-"), ("_DP", "_DN"), ("_T", "_C"))


def _nm(v: float) -> int:
    return round(v * 1e9)


def _h(*parts: object) -> str:
    return "h:" + hashlib.sha1(repr(parts).encode()).hexdigest()[:16]  # noqa: S324 - id only


@dataclass
class _PadRec:
    comp: str | None
    pin: str
    pos: tuple[float, float]
    rot: float
    net: str | None
    def_ref: str
    layers: dict[str, PadShape] = field(default_factory=dict)
    mask: dict[str, PadShape] = field(default_factory=dict)  # "top"/"bottom"
    slot: tuple[float, float, float] | None = None  # (width, length, board angle)


@dataclass
class _Hole:
    pos: tuple[float, float]
    diameter: float
    status: str
    net: str | None
    layer: str
    def_ref: str | None


class Ipc2581Reader:
    """Ingest reader for IPC-2581 revision B and C XML files (entry point ``ipc2581``)."""

    name: ClassVar[str] = "ipc2581"
    extensions: ClassVar[tuple[str, ...]] = (".xml", ".cvg")

    @classmethod
    def can_read(cls, path: Path) -> bool:
        try:
            with Path(path).open("rb") as fh:
                head = fh.read(4096)
        except OSError:
            return False
        return b"<IPC-2581" in head

    def read(self, path: Path, options: ReadOptions | None = None) -> Board:
        opts = options if options is not None else ReadOptions()
        path = Path(path)
        try:
            board = _Parse(path, opts).run()
        except etree.XMLSyntaxError as exc:
            raise IngestError(
                f"{path.name} is not well-formed XML: {exc}", code="INGEST.IPC2581_XML"
            ) from exc
        if opts.strict:
            from oa_model.validate import validate_board

            validate_board(board, strict=True)
        return board


class _Parse:
    def __init__(self, path: Path, opts: ReadOptions) -> None:
        self.path = path
        self.tol = opts.arc_tolerance
        self.diags: list[Diagnostic] = []
        self.rev = "?"
        self.u = 1e-3
        self.lines: dict[str, tuple[float, str]] = {}
        self.std: dict[str, tuple[Any, float]] = {}
        self.user: dict[str, tuple[Any, float]] = {}
        self.specs: dict[str, dict[str, Any]] = {}
        self.finish: str | None = None
        self.layer_defs: dict[str, dict[str, Any]] = {}
        self.layer_order: list[str] = []
        self.stack_el: Any = None
        self.cu_ids: dict[str, str] = {}
        self.stackup: Stackup | None = None
        self.step_name = path.stem
        self.step_seen = False
        self.profile: Any = None
        self.padstack_defs: dict[str, dict[str, Any]] = {}
        self.bom_values: dict[str, str] = {}
        self.comp_els: dict[str, dict[str, Any]] = {}
        self.logical: dict[str, str] = {}
        self.lognets: set[str] = set()
        self.padrecs: dict[Any, _PadRec] = {}
        self.viapads: dict[tuple[int, int], dict[str, PadShape]] = defaultdict(dict)
        self.holes: list[_Hole] = []
        self.slots: list[tuple[Any, ...]] = []
        self.tracks: list[Track] = []
        self.fills: dict[tuple[str | None, str], list[BaseGeometry]] = {}
        self.shapes: list[CopperShape] = []
        self.ids: dict[str, int] = defaultdict(int)
        self.net_names: set[str] = set()
        self.mask_pending: dict[Any, dict[str, PadShape]] = {}
        self._shape_cache: dict[tuple[str, bool], PadShape | None] = {}

    # ------------------------------------------------------------------ helpers
    def diag(self, sev: str, code: str, msg: str, **kw: Any) -> None:
        self.diags.append(Diagnostic(severity=sev, code=code, message=msg, **kw))  # type: ignore[arg-type]

    def uid(self, prefix_hash: str) -> str:
        n = self.ids[prefix_hash]
        self.ids[prefix_hash] += 1
        return prefix_hash if n == 0 else f"{prefix_hash}~{n}"

    def units_of(self, el: Any) -> float:
        name = el.get("units")
        if name is None:
            return self.u
        if name.upper() not in _UNITS:
            self.diag("warning", "INGEST.IPC2581_UNITS", f"unknown units '{name}', using header")
            return self.u
        return _UNITS[name.upper()]

    # ------------------------------------------------------------------ main loop
    def run(self) -> Board:
        in_step = False
        root_seen = False
        for ev, el in etree.iterparse(str(self.path), events=("start", "end"), huge_tree=True):
            tag = local(el)
            if ev == "start":
                if not root_seen:
                    root_seen = True
                    self.rev = el.get("revision", "?")
                if tag == "Step" and not self.step_seen and el.get("type", "BOARD") == "BOARD":
                    self.step_seen = True
                    in_step = True
                    self.step_name = el.get("name", self.step_name)
                    self.finalize_stackup()
                continue
            if tag == "CadHeader":
                self.u = _UNITS.get(el.get("units", "MILLIMETER").upper(), 1e-3)
            elif tag == "EntryLineDesc":
                ld = _child(el, "LineDesc")
                if ld is not None:
                    s = self.units_of(el.getparent())
                    self.lines[el.get("id", "")] = (fnum(ld, "lineWidth", s), ld.get("lineEnd", "ROUND"))
            elif tag == "EntryStandard":
                if len(el):
                    self.std[el.get("id", "")] = (el[0], self.units_of(el.getparent()))
            elif tag == "EntryUser":
                if len(el):
                    self.user[el.get("id", "")] = (el[0], self.units_of(el.getparent()))
            elif tag == "Spec":
                self.read_spec(el)
            elif tag == "Layer" and local(el.getparent()) == "CadData":
                span = _child(el, "Span")
                self.layer_defs[el.get("name", "")] = {
                    "function": el.get("layerFunction", "").upper(),
                    "side": el.get("side", "").upper(),
                    "span": (span.get("fromLayer"), span.get("toLayer")) if span is not None else None,
                }
                self.layer_order.append(el.get("name", ""))
            elif tag == "Stackup" and local(el.getparent()) == "CadData":
                self.stack_el = el
            elif tag == "BomItem":
                for t in el.iter():
                    if local(t) == "Textual" and t.get("textualCharacteristicName") == "Value":
                        self.bom_values[el.get("OEMDesignNumberRef", "")] = t.get(
                            "textualCharacteristicValue", ""
                        )
            elif in_step and local(el.getparent()) == "Step":
                self.step_child(tag, el)
                el.clear()
            elif tag == "Step" and in_step:
                in_step = False
        return self.build()

    def step_child(self, tag: str, el: Any) -> None:
        if tag == "Profile":
            self.profile = _copy_profile(el)
        elif tag == "PadStackDef":
            self.read_padstack_def(el)
        elif tag == "Component":
            loc = _child(el, "Location")
            xf = _child(el, "Xform")
            self.comp_els[el.get("refDes", "")] = {
                "package": el.get("packageRef"),
                "part": el.get("part"),
                "layer": el.get("layerRef", ""),
                "pos": (fnum(loc, "x", self.u), fnum(loc, "y", self.u)) if loc is not None else (0.0, 0.0),
                "rot": math.radians(float(xf.get("rotation", 0))) if xf is not None else 0.0,
            }
        elif tag == "LogicalNet":
            name = el.get("name", "")
            if name:
                self.lognets.add(name)
                for pr in el.iter():
                    if local(pr) == "PinRef":
                        self.logical[f"{pr.get('componentRef')}\x00{pr.get('pin')}"] = name
        elif tag == "LayerFeature":
            self.layer_feature(el)

    # ------------------------------------------------------------------ specs and stackup
    def read_spec(self, el: Any) -> None:
        name = el.get("name", "")
        d: dict[str, Any] = {"text": None, "dk": None, "df": None, "sigma": None}
        for ch in el:
            t = local(ch)
            if t == "SurfaceFinish":
                self.finish = ch.get("type")
            elif t == "General":
                for p in ch:
                    txt = p.get("text")
                    if txt and ":" not in txt and d["text"] is None:
                        d["text"] = txt
                    if txt and txt.lower().startswith("type :"):
                        d["ctype"] = txt.split(":", 1)[1].strip().lower()
            elif t in ("Dielectric", "Conductor"):
                prop = _child(ch, "Property")
                if prop is None or prop.get("value") is None:
                    continue
                v = float(prop.get("value"))
                kind = ch.get("type", "")
                if kind == "DIELECTRIC_CONSTANT":
                    d["dk"] = v
                elif kind == "LOSS_TANGENT":
                    d["df"] = v
                elif kind == "CONDUCTIVITY":
                    unit = (prop.get("unit") or "SIEMENS/M").upper()
                    d["sigma"] = v * {"MHO/CM": 100.0, "SIEMENS/MM": 1000.0, "SIEMENS/CM": 100.0}.get(
                        unit, 1.0
                    )
        if name == "SURFACE_FINISH":
            return
        self.specs[name] = d

    def finalize_stackup(self) -> None:
        if self.stackup is not None:
            return
        cond = [n for n in self.layer_order if self.layer_defs[n]["function"] in _COND_FUNCS]
        if not cond:
            raise IngestError("IPC-2581 file has no conductor layers", code="INGEST.IPC2581_LAYERS")
        entries: list[tuple[str, float, str | None]] = []
        if self.stack_el is not None:
            refs = [e for e in self.stack_el.iter() if local(e) == "StackupLayer"]
            if refs and all(e.get("sequence") is not None for e in refs):
                refs.sort(key=lambda e: int(e.get("sequence", 0)))
            for e in refs:
                sr = _child(e, "SpecRef")
                entries.append(
                    (e.get("layerOrGroupRef", ""), fnum(e, "thickness", self.u), sr.get("id") if sr is not None else None)
                )
            cu_entries = [e for e in entries if e[0] in self.layer_defs and self.layer_defs[e[0]]["function"] in _COND_FUNCS]
            if (
                len(cu_entries) >= 2
                and self.layer_defs[cu_entries[0][0]]["side"] == "BOTTOM"
                and self.layer_defs[cu_entries[-1][0]]["side"] == "TOP"
            ):
                entries.reverse()
        known = bool(entries)
        if not entries:
            self.diag(
                "warning", "INGEST.STACKUP_DEFAULTED", "no Stackup element: default 1.6 mm FR-4 stack"
            )
            n = len(cond)
            gap = (1.6e-3 - n * 35e-6) / max(n - 1, 1)
            for i, c in enumerate(cond):
                entries.append((c, 35e-6, None))
                if i < n - 1:
                    entries.append((f"DIELECTRIC_{i + 1}", gap, None))
                    self.layer_defs[f"DIELECTRIC_{i + 1}"] = {"function": "DIELCORE", "side": "INTERNAL", "span": None}
        self.stackup = self.build_stackup(entries, known)

    def build_stackup(self, entries: list[tuple[str, float, str | None]], known: bool) -> Stackup:
        materials: dict[str, Material] = {}
        keys: dict[tuple[Any, ...], str] = {}

        def material(kind: str, spec_name: str | None, fallback: str) -> str:
            sp = self.specs.get(spec_name or "", {})
            base = sp.get("text") or fallback
            if kind == "conductor":
                base = base.lower()
                sigma = sp.get("sigma") or 5.8e7
                key: tuple[Any, ...] = ("c", base, sigma)
            else:
                dk, df = sp.get("dk"), sp.get("df")
                if dk is None:
                    self.diag("warning", "INGEST.DIELECTRIC_PROPS_MISSING", f"no Dk for '{base}': default 4.2/0.02")
                key = ("d", base, dk, df)
            if key in keys:
                return keys[key]
            name, n = base, 1
            while name in materials:
                n += 1
                name = f"{base}_{n}"
            if kind == "conductor":
                materials[name] = ConductorMaterial(name=name, conductivity=key[2], source="ingest")
            else:
                dk, df = key[2], key[3]
                if dk is None:
                    materials[name] = DielectricMaterial(
                        name=name, model=ConstantDkDf(dk=4.2, df=0.02), source="default"
                    )
                else:
                    materials[name] = DielectricMaterial(
                        name=name, model=ConstantDkDf(dk=max(dk, 1.0), df=df or 0.0), source="ingest"
                    )
            keys[key] = name
            return name

        items: list[tuple[str, str, float, str, str | None]] = []  # kind, ref, t, matname, extra
        for ref, t, spec in entries:
            d = self.layer_defs.get(ref)
            if d is None:
                continue
            fn = d["function"]
            if fn in _COND_FUNCS:
                items.append(("c", ref, t, material("conductor", spec, "copper"), fn))
            elif fn.startswith("DIEL") and t > 0:
                ctype = self.specs.get(spec or "", {}).get("ctype")
                cons = ctype if ctype in ("core", "prepreg") else (
                    "core" if fn == "DIELCORE" else "prepreg" if fn == "DIELPREG" else "unknown"
                )
                items.append(("d", ref, t, material("dielectric", spec, "FR4"), cons))
            elif fn == "SOLDERMASK" and t > 0:
                items.append(("m", ref, t, material("dielectric", spec, "SOLDERMASK"), d["side"]))
        total = sum(i[2] for i in items)
        z = total
        layers: list[Any] = []
        ci = di = 0
        seen_mask: set[str] = set()
        for kind, ref, t, mat, extra in items:
            z_top, z_bot = z, z - t
            z = z_bot
            if kind == "c":
                ci += 1
                self.cu_ids[ref] = f"L{ci}"
                layers.append(
                    ConductorLayer(
                        id=f"L{ci}", index=ci, source_name=ref, thickness=t, z_bottom=z_bot,
                        z_top=z_top, material=mat,
                        role="plane" if extra == "PLANE" else "mixed" if extra == "MIXED" else "signal",
                    )
                )
            elif kind == "d":
                di += 1
                layers.append(
                    DielectricLayer(
                        id=f"D{di}", index=di, source_name=ref, construction=extra,  # type: ignore[arg-type]
                        thickness=t, z_bottom=z_bot, z_top=z_top, material=mat,
                    )
                )
            else:
                mid = "MB" if extra == "BOTTOM" else "MT"
                if mid in seen_mask:
                    continue
                seen_mask.add(mid)
                layers.append(
                    MaskLayer(id=mid, source_name=ref, thickness=t, z_bottom=z_bot, z_top=z_top, material=mat)
                )
        # recompute z so the bottom-most layer starts at 0 even when masks were skipped
        shift = layers[-1].z_bottom if layers else 0.0
        if abs(shift) > 1e-15:
            layers = [l.model_copy(update={"z_bottom": l.z_bottom - shift, "z_top": l.z_top - shift}) for l in layers]
        used = {l.material for l in layers}
        return Stackup(
            layers=tuple(layers),
            materials=tuple(m for n, m in materials.items() if n in used),
            finish=self.finish,
            total_thickness=layers[0].z_top,
            known=known,
        )

    # ------------------------------------------------------------------ primitives
    def prim_geom(self, ref_el: Any) -> tuple[PadShape | None, BaseGeometry | None]:
        """(native shape, geometry) of a StandardPrimitiveRef / UserPrimitiveRef / inline primitive."""
        kind = local(ref_el)
        if kind == "StandardPrimitiveRef":
            ent = self.std.get(ref_el.get("id", ""))
            if ent is None:
                return None, None
            el, s = ent
            k = local(el)
            if k == "Contour":
                return None, self.contour_geom(el, s)
            return standard_shape(k, el, s), polygon_prim(k, el, s, self.tol)
        if kind == "UserPrimitiveRef":
            ent = self.user.get(ref_el.get("id", ""))
            if ent is None:
                return None, None
            return None, self.special_geom(ent[0], ent[1])
        return None, None

    def special_geom(self, el: Any, s: float) -> BaseGeometry | None:
        """Geometry of a UserSpecial / UserPrimitive element tree (pad-local)."""
        parts: list[BaseGeometry] = []
        for ch in el:
            k = local(ch)
            ld = _child(ch, "LineDesc")
            fd = _child(ch, "FillDesc")
            width = fnum(ld, "lineWidth", s) if ld is not None else 0.0
            end = ld.get("lineEnd", "ROUND") if ld is not None else "ROUND"
            hollow = fd is not None and fd.get("fillProperty", "SOLID") in ("HOLLOW", "VOID")
            g: BaseGeometry | None = None
            if k == "Contour":
                g = self.contour_geom(ch, s)
            elif k == "Line":
                a = (fnum(ch, "startX", s), fnum(ch, "startY", s))
                b = (fnum(ch, "endX", s), fnum(ch, "endY", s))
                g = stroke(LineString([a, b]), width, end, self.tol)
                width = 0.0
            elif k == "Polyline":
                verts = poly_vertices(ch, s)
                ldr = _child(ch, "LineDescRef")
                if ldr is not None:
                    width, end = self.lines.get(ldr.get("id", ""), (width, end))
                g = stroke(LineString(path_points(verts, self.tol)), width, end, self.tol)
                width = 0.0
            elif k == "Circle":
                g = circle_geom(fnum(ch, "diameter", s), self.tol)
                if hollow:
                    g = stroke(g.exterior, width, end, self.tol)
                    width = 0.0
            else:
                g = polygon_prim(k, ch, s, self.tol)
                if g is not None and hollow:
                    g = stroke(g.boundary, width, end, self.tol)
                    width = 0.0
            if g is not None and not g.is_empty:
                parts.append(g)
        return union(parts) if parts else None

    def contour_geom(self, el: Any, s: float) -> BaseGeometry | None:
        poly = _child(el, "Polygon")
        if poly is None:
            return None
        g: BaseGeometry = ring_polygon(poly_vertices(poly, s), self.tol)
        for cut in el:
            if local(cut) == "Cutout":
                g = g.difference(ring_polygon(poly_vertices(cut, s), self.tol))
        return g

    def pad_shape(self, ref_el: Any, mirror: bool) -> PadShape | None:
        key = (f"{local(ref_el)}:{ref_el.get('id', '')}", mirror)
        if key in self._shape_cache:
            return self._shape_cache[key]
        native, geom = self.prim_geom(ref_el)
        shape: PadShape | None
        if native is not None and (not mirror or native.kind in ("circle", "rect", "obround", "roundrect")):
            shape = native
        else:
            if geom is not None and mirror:
                geom = xform_geom(geom, 0.0, True, 0.0, 0.0)
            shape = pad_shape_from_geom(geom) if geom is not None else None
            if shape is None:
                self.diag("warning", "INGEST.PRIMITIVE_UNSUPPORTED", f"cannot convert primitive '{ref_el.get('id')}'")
        self._shape_cache[key] = shape
        return shape

    # ------------------------------------------------------------------ padstack defs
    def read_padstack_def(self, el: Any) -> None:
        hole = _child(el, "PadstackHoleDef")
        d: dict[str, Any] = {"hole": None}
        if hole is not None:
            d["hole"] = (
                fnum(hole, "diameter", self.u),
                hole.get("platingStatus", "PLATED").upper(),
                (fnum(hole, "x", self.u), fnum(hole, "y", self.u)),
            )
        self.padstack_defs[el.get("name", "")] = d

    # ------------------------------------------------------------------ layer features
    def layer_feature(self, lf: Any) -> None:
        ref = lf.get("layerRef", "")
        d = self.layer_defs.get(ref)
        if d is None:
            return
        fn = d["function"]
        if fn in _COND_FUNCS:
            lid = self.cu_ids.get(ref)
            if lid is None:
                self.diag("warning", "INGEST.LAYER_NOT_IN_STACKUP", f"conductor layer '{ref}' not in stackup")
                return
            for st in lf:
                if local(st) == "Set":
                    self.copper_set(st, lid)
        elif fn == "SOLDERMASK":
            side = "bottom" if d["side"] == "BOTTOM" else "top"
            for st in lf:
                if local(st) == "Set":
                    for p in st:
                        if local(p) == "Pad":
                            self.mask_pad(p, side)
        elif fn in ("DRILL", "ROUT") or "BACKDRILL" in fn:
            span = d["span"]
            for st in lf:
                if local(st) != "Set":
                    continue
                net = st.get("net") or None
                for h in st:
                    k = local(h)
                    if k == "Hole":
                        pos = (fnum(h, "x", self.u), fnum(h, "y", self.u))
                        self.holes.append(
                            _Hole(pos, fnum(h, "diameter", self.u), h.get("platingStatus", "PLATED").upper(),
                                  net, ref if span else ref, st.get("geometry"))
                        )
                    elif k == "SlotCavity":
                        self.slots.append((h, net, d))

    def mask_pad(self, p: Any, side: str) -> None:
        pr = _child(p, "PinRef")
        loc = _child(p, "Location")
        prim = _prim_child(p)
        if pr is None or loc is None or prim is None:
            return
        xf = _child(p, "Xform")
        mirror = xf is not None and xf.get("mirror", "false").lower() == "true"
        shape = self.pad_shape(prim, mirror)
        if shape is None:
            return
        key = (pr.get("componentRef"), pr.get("pin"), _nm(fnum(loc, "x", self.u)), _nm(fnum(loc, "y", self.u)))
        self.mask_pending.setdefault(key, {})[side] = shape

    def copper_set(self, st: Any, lid: str) -> None:
        net = st.get("net") or None
        if net:
            self.net_names.add(net)
        via_set = st.get("padUsage", "").upper() == "VIA"
        for ch in st:
            k = local(ch)
            if k == "Pad":
                self.copper_pad(ch, lid, net, via_set)
            elif k == "Features":
                off = _child(ch, "Location")
                dx = fnum(off, "x", self.u) if off is not None else 0.0
                dy = fnum(off, "y", self.u) if off is not None else 0.0
                for f in ch:
                    self.feature(f, lid, net, dx, dy)

    def copper_pad(self, p: Any, lid: str, net: str | None, via_set: bool) -> None:
        loc = _child(p, "Location")
        prim = _prim_child(p)
        if loc is None or prim is None:
            return
        x, y = fnum(loc, "x", self.u), fnum(loc, "y", self.u)
        xf = _child(p, "Xform")
        rot = math.radians(float(xf.get("rotation", 0))) if xf is not None else 0.0
        mirror = xf is not None and xf.get("mirror", "false").lower() == "true"
        shape = self.pad_shape(prim, mirror)
        if shape is None:
            return
        pr = _child(p, "PinRef")
        def_ref = p.get("padstackDefRef", "")
        hole = self.padstack_defs.get(def_ref, {}).get("hole")
        if pr is None and (via_set or (hole is not None and hole[1] == "VIA")):
            self.viapads[(_nm(x), _nm(y))][lid] = shape
            return
        comp = pr.get("componentRef") if pr is not None else None
        pin = pr.get("pin", "") if pr is not None else ""
        key = (comp, pin, _nm(x), _nm(y)) if comp else ("free", def_ref, _nm(x), _nm(y))
        rec = self.padrecs.get(key)
        if rec is None:
            if net is None and comp:
                net = self.logical.get(f"{comp}\x00{pin}")
            rec = self.padrecs[key] = _PadRec(comp, pin, (x, y), rot, net, def_ref)
        rec.layers[lid] = shape

    def feature(self, f: Any, lid: str, net: str | None, dx: float, dy: float) -> None:
        k = local(f)
        s = self.u
        if k == "UserSpecial":
            for sub in f:
                self.feature(sub, lid, net, dx, dy)
            return
        if k in ("Line", "Arc", "Polyline"):
            ld = _child(f, "LineDesc")
            ldr = _child(f, "LineDescRef")
            if ld is not None:
                width, end = fnum(ld, "lineWidth", s), ld.get("lineEnd", "ROUND")
            elif ldr is not None:
                width, end = self.lines.get(ldr.get("id", ""), (0.0, "ROUND"))
            else:
                width, end = 0.0, "ROUND"
            if k == "Polyline":
                verts = [(x + dx, y + dy, (c[0] + dx, c[1] + dy) if c else None, cw) for x, y, c, cw in poly_vertices(f, s)]
            elif k == "Line":
                verts = [
                    (fnum(f, "startX", s) + dx, fnum(f, "startY", s) + dy, None, False),
                    (fnum(f, "endX", s) + dx, fnum(f, "endY", s) + dy, None, False),
                ]
            else:
                verts = [
                    (fnum(f, "startX", s) + dx, fnum(f, "startY", s) + dy, None, False),
                    (
                        fnum(f, "endX", s) + dx, fnum(f, "endY", s) + dy,
                        (fnum(f, "centerX", s) + dx, fnum(f, "centerY", s) + dy),
                        f.get("clockwise", "false").lower() == "true",
                    ),
                ]
            if width <= 0 or len(verts) < 2:
                return
            if net:
                tr = self.make_track(verts, lid, net, width, end)
                if tr is not None:
                    self.tracks.append(tr)
            else:
                g = stroke(LineString(path_points(verts, self.tol)), width, end, self.tol)
                self.add_shape(g, lid, None)
            return
        # filled geometry
        g: BaseGeometry | None
        if k == "Contour":
            g = self.contour_geom(f, s)
            if g is not None and (dx or dy):
                g = xform_geom(g, 0.0, False, dx, dy)
            if g is not None and net:
                self.fills.setdefault((net, lid), []).append(g)
                return
        elif k in ("UserPrimitiveRef", "StandardPrimitiveRef"):
            _, g = self.prim_geom(f)
            if g is not None and (dx or dy):
                g = xform_geom(g, 0.0, False, dx, dy)
        else:
            g = polygon_prim(k, f, s, self.tol)
            if g is not None and (dx or dy):
                g = xform_geom(g, 0.0, False, dx, dy)
        if g is not None:
            self.add_shape(g, lid, net)

    def add_shape(self, g: BaseGeometry, lid: str, net: str | None) -> None:
        for poly in to_model_polygons(g):
            sid = self.uid(_h("shape", lid, net, _nm(poly.exterior.array[0][0]), _nm(poly.exterior.array[0][1]), len(poly.exterior)))
            self.shapes.append(CopperShape(id=sid, net=net, layer=lid, polygon=poly, source=SourceRef(tool="ipc2581")))  # type: ignore[arg-type]

    def make_track(self, verts: list[Vertex], lid: str, net: str, width: float, end: str) -> Track | None:
        path: list[PathSeg] = []
        cur = (verts[0][0], verts[0][1])
        for x, y, c, cw in verts[1:]:
            nxt = (x, y)
            if c is None:
                if math.hypot(nxt[0] - cur[0], nxt[1] - cur[1]) > 0:
                    path.append(LineSeg(start=cur, end=nxt))
            else:
                path.extend(_arcs(cur, nxt, c, cw))
            cur = nxt
        if not path:
            return None
        tid = self.uid(_h("track", lid, net, width, tuple((round(s.start[0] * 1e9), round(s.start[1] * 1e9), round(s.end[0] * 1e9), round(s.end[1] * 1e9)) for s in path)))
        caps = {"ROUND": "round", "SQUARE": "square", "NONE": "flat"}.get(end.upper(), "round")
        return Track(id=tid, net=net, layer=lid, width=width, path=tuple(path), end_caps=caps, source=SourceRef(tool="ipc2581"))  # type: ignore[arg-type]

    # ------------------------------------------------------------------ assembly
    def build(self) -> Board:
        if self.stackup is None:
            raise IngestError("IPC-2581 file has no Step of type BOARD", code="INGEST.IPC2581_STEP")
        st = self.stackup
        cu_list = [l.id for l in st.layers if l.kind == "conductor"]
        padstacks: dict[str, Padstack] = {}
        ps_keys: dict[tuple[Any, ...], str] = {}

        def padstack_for(def_ref: str, drill: Drill | None, layers: dict[str, PadShape], mask: dict[str, PadShape]) -> str:
            lay = tuple(PadstackLayer(layer=lid, pad=layers[lid]) for lid in cu_list if lid in layers)
            key = (def_ref, drill.model_dump_json() if drill else None, tuple((l.layer, l.pad.model_dump_json()) for l in lay if l.pad), tuple(sorted((k, v.model_dump_json()) for k, v in mask.items())))
            if key in ps_keys:
                return ps_keys[key]
            pid = f"i:{def_ref}" if f"i:{def_ref}" not in padstacks else f"i:{def_ref}#{sum(1 for p in padstacks if p.startswith(f'i:{def_ref}')) + 1}"
            padstacks[pid] = Padstack(id=pid, drill=drill, layers=lay, mask_opening_top=mask.get("top"), mask_opening_bottom=mask.get("bottom"))
            ps_keys[key] = pid
            return pid

        # ---- slots that belong to a component pad
        pos_index: dict[tuple[int, int], list[_PadRec]] = defaultdict(list)
        for rec in self.padrecs.values():
            pos_index[(round(rec.pos[0] / 2e-7), round(rec.pos[1] / 2e-7))].append(rec)
        free_slots: list[tuple[Any, ...]] = []
        for slot in self.slots:
            el, net, d = slot
            shp = _child(el, "Oval")
            if shp is None:
                shp = _child(el, "RectCenter")
            loc = _child(el, "Location")
            if shp is None:
                continue
            w, hh = fnum(shp, "width", self.u), fnum(shp, "height", self.u)
            pos = (fnum(loc, "x", self.u), fnum(loc, "y", self.u)) if loc is not None else (0.0, 0.0)
            hit = None
            kx, ky = round(pos[0] / 2e-7), round(pos[1] / 2e-7)
            for i in (-1, 0, 1):
                for j in (-1, 0, 1):
                    for rec in pos_index.get((kx + i, ky + j), ()):
                        hit = hit or rec
            if hit is None:
                free_slots.append(slot)
            else:
                hit.slot = (min(w, hh), max(w, hh), 0.0 if w >= hh else math.pi / 2)
                hole_keys.add((kx, ky))

        # ---- pads
        pads: list[Pad] = []
        comp_pads: dict[str, list[str]] = defaultdict(list)
        hole_keys: set[tuple[int, int]] = set()
        used_ids: dict[str, int] = defaultdict(int)
        for key, rec in self.padrecs.items():
            hd = self.padstack_defs.get(rec.def_ref, {}).get("hole")
            drill = None
            if hd is not None:
                dia, status, off = hd
                drill = Drill(kind="npth" if status == "NONPLATED" else "plated", diameter=dia, offset=off)
                if rec.slot is not None:
                    rel = (rec.slot[2] - rec.rot) % math.pi
                    if min(rel, math.pi - rel) < 1e-6:
                        drill = Drill(kind=drill.kind, shape="slot", diameter=rec.slot[0], slot_length=rec.slot[1], offset=off)
                    else:
                        self.diag("warning", "INGEST.SLOT_ORIENTATION", f"slot of {rec.comp}.{rec.pin} is not aligned with the pad frame: round drill kept")
                c, s_ = math.cos(rec.rot), math.sin(rec.rot)
                hx = rec.pos[0] + off[0] * c - off[1] * s_
                hy = rec.pos[1] + off[0] * s_ + off[1] * c
                hole_keys.add((round(hx / 2e-7), round(hy / 2e-7)))
            mask = self.mask_pending.get((rec.comp, rec.pin, _nm(rec.pos[0]), _nm(rec.pos[1])), {}) if rec.comp else {}
            psid = padstack_for(rec.def_ref or "pad", drill, rec.layers, mask)
            base = f"i:{rec.comp}.{rec.pin}" if rec.comp else _h("pad", rec.def_ref, _nm(rec.pos[0]), _nm(rec.pos[1]))
            n = used_ids[base]
            used_ids[base] += 1
            pid = base if n == 0 else f"{base}#{n}"
            kind = "smd" if drill is None else ("npth" if drill.kind == "npth" else "through_hole")
            pads.append(Pad(id=pid, component=rec.comp, number=rec.pin, padstack=psid, position=rec.pos, rotation=rec.rot, net=rec.net, kind=kind, source=SourceRef(tool="ipc2581", name=f"{rec.comp}.{rec.pin}" if rec.comp else None)))  # type: ignore[arg-type]
            if rec.comp:
                comp_pads[rec.comp].append(pid)
            if rec.net:
                self.net_names.add(rec.net)

        # ---- vias, mechanical holes
        vias: list[Via] = []
        backdrills: dict[tuple[int, int], list[Backdrill]] = defaultdict(list)
        via_holes: list[_Hole] = []
        for h in self.holes:
            d = self.layer_defs.get(h.layer, {})
            if "BACKDRILL" in d.get("function", "") or "BACKDRILL" in h.layer.upper():
                span = d.get("span")
                if span and span[0] in self.cu_ids and span[1] in self.cu_ids:
                    top_side = cu_list.index(self.cu_ids[span[0]]) == 0
                    last = self.cu_ids[span[1]]
                    idx = cu_list.index(last)
                    nxt = cu_list[idx + 1] if top_side and idx + 1 < len(cu_list) else (cu_list[idx - 1] if idx > 0 else None)
                    backdrills[(_nm(h.pos[0]), _nm(h.pos[1]))].append(
                        Backdrill(side="top" if top_side else "bottom", diameter=h.diameter, last_removed_layer=last, must_not_cut=nxt)  # type: ignore[arg-type]
                    )
                continue
            if h.status == "VIA":
                via_holes.append(h)
                continue
            hk = (round(h.pos[0] / 2e-7), round(h.pos[1] / 2e-7))
            if any((hk[0] + i, hk[1] + j) in hole_keys for i in (-1, 0, 1) for j in (-1, 0, 1)):
                continue
            hole_keys.add(hk)
            drill = Drill(kind="npth" if h.status == "NONPLATED" else "plated", diameter=h.diameter)
            psid = padstack_for(h.def_ref or "hole", drill, {}, {})
            pid = _h("pad", "hole", _nm(h.pos[0]), _nm(h.pos[1]))
            used_ids[pid] += 1
            if used_ids[pid] > 1:
                pid = f"{pid}#{used_ids[pid] - 1}"
            pads.append(Pad(id=pid, component=None, number="", padstack=psid, position=h.pos, rotation=0.0, net=h.net, kind="npth" if drill.kind == "npth" else "through_hole", source=SourceRef(tool="ipc2581")))  # type: ignore[arg-type]
        for el, net, d in free_slots:
            shp = _child(el, "Oval")
            if shp is None:
                shp = _child(el, "RectCenter")
            loc = _child(el, "Location")
            if shp is None:
                continue
            w, hh = fnum(shp, "width", self.u), fnum(shp, "height", self.u)
            pos = (fnum(loc, "x", self.u), fnum(loc, "y", self.u)) if loc is not None else (0.0, 0.0)
            status = el.get("platingStatus", "NONPLATED").upper()
            drill = Drill(kind="npth" if status == "NONPLATED" else "plated", shape="slot", diameter=min(w, hh), slot_length=max(w, hh))
            rot = 0.0 if w >= hh else math.pi / 2
            psid = padstack_for("slot", drill, {}, {})
            pid = _h("pad", "slot", _nm(pos[0]), _nm(pos[1]))
            pads.append(Pad(id=pid, component=None, number="", padstack=psid, position=pos, rotation=rot, net=net, kind="npth" if drill.kind == "npth" else "through_hole", source=SourceRef(tool="ipc2581")))  # type: ignore[arg-type]

        for h in via_holes:
            pk = (_nm(h.pos[0]), _nm(h.pos[1]))
            span = self.layer_defs.get(h.layer, {}).get("span")
            s_id = self.cu_ids.get(span[0]) if span else None
            e_id = self.cu_ids.get(span[1]) if span else None
            if s_id is None or e_id is None:
                s_id, e_id = cu_list[0], cu_list[-1]
            if cu_list.index(s_id) > cu_list.index(e_id):
                s_id, e_id = e_id, s_id
            kind = "through" if (s_id, e_id) == (cu_list[0], cu_list[-1]) else (
                "blind" if s_id == cu_list[0] or e_id == cu_list[-1] else "buried")
            drill = Drill(kind="plated", diameter=h.diameter)
            psid = padstack_for(h.def_ref or "via", drill, self.viapads.get(pk, {}), {})
            vid = self.uid(_h("via", h.net, pk, s_id, e_id))
            vias.append(Via(id=vid, padstack=psid, position=h.pos, net=h.net, kind=kind, start_layer=s_id, end_layer=e_id, backdrills=tuple(backdrills.get(pk, ())), source=SourceRef(tool="ipc2581")))  # type: ignore[arg-type]
            if h.net:
                self.net_names.add(h.net)

        # ---- zones from fills
        zones: list[Zone] = []
        for (net, lid), geoms in self.fills.items():
            polys: list[Polygon] = []
            for g in geoms:
                polys.extend(to_model_polygons(g))
            if not polys:
                continue
            pts = [p for poly in polys for p in poly.exterior.array]
            hull = MultiPoint([tuple(p) for p in pts]).convex_hull
            outline = (to_model_polygons(hull) or polys[:1])[0]
            zid = self.uid(_h("zone", net, lid))
            zones.append(Zone(id=zid, net=net, layers=(lid,), outline=outline, fills=(ZoneFill(layer=lid, polygons=tuple(polys)),), fill_state="filled", source=SourceRef(tool="ipc2581")))  # type: ignore[arg-type]
        if zones:
            self.diag("info", "INGEST.ZONE_OUTLINE_APPROX", "IPC-2581 has no zone objects: zone outlines are the convex hull of the filled copper, priority and hatch are unknown")

        # ---- components
        bom = self.bom_values
        comps: list[Component] = []
        for ref, c in self.comp_els.items():
            side = "bottom" if self.layer_defs.get(c["layer"], {}).get("side") == "BOTTOM" else "top"
            comps.append(Component(refdes=ref, value=bom.get(c["part"] or "") or None, footprint=c["package"], side=side, position=c["pos"], rotation=c["rot"], pads=tuple(comp_pads.get(ref, ())), source=SourceRef(tool="ipc2581", name=ref)))  # type: ignore[arg-type]

        for k in self.logical:
            pass
        nets_all = sorted(self.net_names | self.lognets)
        nets = tuple(Net(id=n, kind=_net_kind(n), source=SourceRef(tool="ipc2581", name=n)) for n in nets_all)  # type: ignore[arg-type]
        stat = self.path.read_bytes()
        meta = BoardMeta(
            name=self.step_name, source_tool="ipc2581", source_version=f"IPC-2581{self.rev}",
            source_file=self.path.name, source_hash=hashlib.sha256(stat).hexdigest(),
            reader=READER_NAME, arc_tolerance=self.tol,
            created_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        return Board(
            meta=meta, outline=self.build_outline(), stackup=st, nets=nets,
            diff_pairs=_diff_pairs(nets_all),
            padstacks=tuple(padstacks.values()), pads=tuple(pads), vias=tuple(vias),
            tracks=tuple(self.tracks), zones=tuple(zones), shapes=tuple(self.shapes),
            components=tuple(comps), diagnostics=tuple(self.diags),
        )

    def build_outline(self) -> BoardOutline:
        prof = self.profile
        if prof is None:
            self.diag("warning", "INGEST.NO_OUTLINE", "Step has no Profile")
            return BoardOutline(polygon=Polygon(exterior=_pts([(0, 0), (1e-3, 0), (1e-3, 1e-3)])))  # type: ignore[arg-type]
        poly_el = _child(prof, "Polygon")
        verts = poly_vertices(poly_el, self.u)
        ext = ring_polygon(verts, self.tol)
        g: BaseGeometry = ext
        for cut in prof:
            if local(cut) == "Cutout":
                g = g.difference(ring_polygon(poly_vertices(cut, self.u), self.tol))
        polys = to_model_polygons(g)
        main = max(polys, key=lambda p: abs(_area(p))) if polys else Polygon(exterior=_pts([(0, 0), (1e-3, 0), (1e-3, 1e-3)]))  # type: ignore[arg-type]
        edges = _edges(verts)
        return BoardOutline(polygon=main, edges=edges, extra_boards=tuple(p for p in polys if p is not main))


# ---------------------------------------------------------------------- module helpers
def _pts(points: list[tuple[float, float]]) -> Any:
    from oa_model.arrays import Points

    return Points(points)


def _area(p: Polygon) -> float:
    from oa_model.geometry import signed_area

    return signed_area(p.exterior)


def _child(el: Any, name: str) -> Any:
    for c in el:
        if local(c) == name:
            return c
    return None


def _prim_child(el: Any) -> Any:
    for c in el:
        if local(c) in ("StandardPrimitiveRef", "UserPrimitiveRef"):
            return c
    return None


def _copy_profile(el: Any) -> Any:
    import copy

    return copy.deepcopy(el)


def poly_vertices(el: Any, s: float) -> list[Vertex]:
    """PolyBegin / PolyStepSegment / PolyStepCurve children of a Polygon or Polyline."""
    out: list[Vertex] = []
    for c in el:
        k = local(c)
        if k == "PolyBegin" or k == "PolyStepSegment":
            out.append((fnum(c, "x", s), fnum(c, "y", s), None, False))
        elif k == "PolyStepCurve":
            out.append(
                (
                    fnum(c, "x", s), fnum(c, "y", s),
                    (fnum(c, "centerX", s), fnum(c, "centerY", s)),
                    c.get("clockwise", "false").lower() == "true",
                )
            )
    return out


def _fix_center(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> tuple[float, float]:
    """Move the centre onto the chord bisector so |a-c| == |b-c| (removes 1 nm rounding skew)."""
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    if L == 0:
        return c
    ux, uy = dx / L, dy / L
    t = (c[0] - mx) * -uy + (c[1] - my) * ux
    return (mx - uy * t, my + ux * t)


def _arcs(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], cw: bool
) -> list[PathSeg]:
    if math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-12:  # full circle: two half arcs
        opp = (2 * c[0] - a[0], 2 * c[1] - a[1])
        return [ArcSeg(start=a, end=opp, center=c, ccw=not cw), ArcSeg(start=opp, end=a, center=c, ccw=not cw)]
    return [ArcSeg(start=a, end=b, center=_fix_center(a, b, c), ccw=not cw)]


def _edges(verts: list[Vertex]) -> tuple[PathSeg, ...]:
    """Analytic exterior edges, CCW."""
    segs: list[PathSeg] = []
    cur = (verts[0][0], verts[0][1])
    for x, y, c, cw in verts[1:]:
        nxt = (x, y)
        if c is None:
            if nxt != cur:
                segs.append(LineSeg(start=cur, end=nxt))
        else:
            segs.extend(_arcs(cur, nxt, c, cw))
        cur = nxt
    if not segs:
        return ()
    pts = path_points(verts, 1e-6)
    area2 = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
    if area2 < 0:
        rev: list[PathSeg] = []
        for sg in reversed(segs):
            if isinstance(sg, LineSeg):
                rev.append(LineSeg(start=sg.end, end=sg.start))
            else:
                rev.append(ArcSeg(start=sg.end, end=sg.start, center=sg.center, ccw=not sg.ccw))
        segs = rev
    return tuple(segs)


def _net_kind(name: str) -> str:
    if _GROUND_RE.search(name):
        return "ground"
    if _POWER_RE.match(name.lstrip("/")):
        return "power"
    return "signal"


def _diff_pairs(names: list[str]) -> tuple[DiffPair, ...]:
    have = set(names)
    out: list[DiffPair] = []
    seen: set[str] = set()
    for n in names:
        for sp, sn in _PAIR_SUFFIXES:
            if n.upper().endswith(sp) and len(n) > len(sp):
                base = n[: -len(sp)]
                for cand in have:
                    if cand.upper() == (base + sn).upper() and n not in seen and cand != n:
                        seen.update((n, cand))
                        out.append(DiffPair(id=f"dp:{base}", p_net=n, n_net=cand, origin="naming"))
    return tuple(out)


Ipc2581Reader.__module__ = __name__
