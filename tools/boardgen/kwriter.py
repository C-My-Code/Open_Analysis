# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic KiCad ``.kicad_pcb`` / ``.kicad_pro`` text writer (KiCad 10 and 9 syntax).

Pure standard library.  All geometry is in millimetres (KiCad file units, Y down).
Every uuid is derived with ``uuid5`` from the board name and a per-kind counter, so
re-running the generator yields byte-identical output.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from typing import Any

NS = uuid.UUID("6f1c0d2e-4b52-4a77-9a50-0a9e1b0c0102")

COPPER_T = 0.035  # 1 oz, mm
DK = 4.3
DF = 0.02


class Q(str):
    """A quoted S-expression string."""


def num(v: float | int) -> str:
    if isinstance(v, int):
        return str(v)
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _atom(a: Any) -> str:
    if isinstance(a, Q):
        return '"' + str(a).replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(a, bool):
        return "yes" if a else "no"
    if isinstance(a, (int, float)):
        return num(a)
    return str(a)


def fmt(node: list, depth: int = 0) -> str:
    """KiCad-like indentation: leading atoms inline, child lists on their own lines."""
    pad = "\t" * depth
    if not any(isinstance(c, list) for c in node):
        return pad + "(" + " ".join(_atom(c) for c in node) + ")"
    head = [c for c in node if not isinstance(c, list)]
    # keep leading atoms only (atoms after the first list are rare; emit inline anyway)
    first_list = next(i for i, c in enumerate(node) if isinstance(c, list))
    head = node[:first_list]
    rest = node[first_list:]
    out = [pad + "(" + " ".join(_atom(c) for c in head)]
    for c in rest:
        if isinstance(c, list):
            out.append(fmt(c, depth + 1))
        else:
            out.append("\t" * (depth + 1) + _atom(c))
    out.append(pad + ")")
    return "\n".join(out)


def r6(v: float) -> float:
    return round(v + 0.0, 6)


def metres(mm: float) -> float:
    return round(mm * 1e-3, 12)


def cu_names(n: int) -> list[str]:
    return ["F.Cu"] + [f"In{i}.Cu" for i in range(1, n - 1)] + ["B.Cu"]


def cu_ids(n: int) -> dict[str, int]:
    ids = {"F.Cu": 0, "B.Cu": 2}
    for i in range(1, n - 1):
        ids[f"In{i}.Cu"] = 2 * i + 2
    return ids


TECH_LAYERS = [
    (9, "F.Adhes", "user", "F.Adhesive"), (11, "B.Adhes", "user", "B.Adhesive"),
    (13, "F.Paste", "user", None), (15, "B.Paste", "user", None),
    (5, "F.SilkS", "user", "F.Silkscreen"), (7, "B.SilkS", "user", "B.Silkscreen"),
    (1, "F.Mask", "user", None), (3, "B.Mask", "user", None),
    (17, "Dwgs.User", "user", "User.Drawings"), (19, "Cmts.User", "user", "User.Comments"),
    (21, "Eco1.User", "user", "User.Eco1"), (23, "Eco2.User", "user", "User.Eco2"),
    (25, "Edge.Cuts", "user", None), (27, "Margin", "user", None),
    (31, "F.CrtYd", "user", "F.Courtyard"), (29, "B.CrtYd", "user", "B.Courtyard"),
    (35, "F.Fab", "user", None), (33, "B.Fab", "user", None),
]


@dataclass
class Stack:
    """Physical stackup, top to bottom: alternating copper / dielectric."""

    cu_t: list[float]  # copper thickness per layer (mm)
    diel: list[tuple[float, str]]  # (thickness mm, 'core'|'prepreg') between copper layers

    def __post_init__(self) -> None:
        assert len(self.diel) == len(self.cu_t) - 1

    @property
    def ncu(self) -> int:
        return len(self.cu_t)

    def total_nm(self) -> int:
        return sum(round(t * 1e6) for t in self.cu_t) + sum(round(t * 1e6) for t, _ in self.diel)

    def rows(self) -> list[dict]:
        """Top-to-bottom rows with SI z values (z = 0 at the board bottom)."""
        names = cu_names(self.ncu)
        seq: list[tuple[str, str, str, int]] = []  # id, source, kind, thickness nm
        for i in range(self.ncu):
            seq.append((f"L{i + 1}", names[i], "conductor", round(self.cu_t[i] * 1e6)))
            if i < self.ncu - 1:
                t, c = self.diel[i]
                seq.append((f"D{i + 1}", f"dielectric {i + 1}", "dielectric", round(t * 1e6)))
        z = self.total_nm()
        rows = []
        for lid, src, kind, t in seq:
            rows.append({
                "id": lid, "source_name": src, "kind": kind,
                "thickness": round(t * 1e-9, 12), "z_top": round(z * 1e-9, 12), "z_bottom": round((z - t) * 1e-9, 12),
            })
            z -= t
        return rows

    def sexpr(self) -> list:
        names = cu_names(self.ncu)
        node: list = ["stackup"]
        for i in range(self.ncu):
            node.append(["layer", Q(names[i]), ["type", Q("copper")], ["thickness", self.cu_t[i]]])
            if i < self.ncu - 1:
                t, c = self.diel[i]
                node.append(["layer", Q(f"dielectric {i + 1}"), ["type", Q(c)], ["thickness", t],
                             ["material", Q("FR4")], ["epsilon_r", DK], ["loss_tangent", DF]])
        node.append(["copper_finish", Q("None")])
        node.append(["dielectric_constraints", "no"])
        return node


def stack(n: int, diels: list[tuple[float, str]]) -> Stack:
    return Stack([COPPER_T] * n, diels)


# Stackups (thicknesses in mm).  All FR-4-like: Dk 4.3, Df 0.02, 35 um copper.
STACK4 = stack(4, [(0.2, "prepreg"), (1.06, "core"), (0.2, "prepreg")])  # 1.6 mm
STACK4_PWR = stack(4, [(0.2, "prepreg"), (0.1, "core"), (1.16, "prepreg")])  # 1.6 mm, thin L2-L3
STACK6 = stack(6, [(0.2, "prepreg"), (0.2, "core"), (0.2, "prepreg"), (0.2, "core"), (0.59, "prepreg")])  # 1.6 mm
STACK8 = stack(8, [(0.2, "prepreg"), (0.2, "core"), (0.2, "prepreg"), (0.42, "core"),
                   (0.15, "prepreg"), (0.35, "core"), (0.2, "prepreg")])  # 2.0 mm


@dataclass
class Board:
    name: str
    stk: Stack
    size: tuple[float, float]
    kicad: int = 10  # 10 or 9 (syntax flavour)
    nets: dict[str, int] = field(default_factory=lambda: {"": 0})
    body: list[list] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    netclasses: list[dict] = field(default_factory=list)
    patterns: list[tuple[str, str]] = field(default_factory=list)
    rules: dict[str, float] = field(default_factory=dict)
    exp: dict[str, Any] = field(default_factory=lambda: {
        "geometry": {}, "erc": [], "negative_cases": [], "unspecified": [], "notes": []})
    gen_counts: dict[str, int] = field(default_factory=dict)

    # ---- bookkeeping -------------------------------------------------
    def uid(self, kind: str) -> str:
        n = self.counters.get(kind, 0) + 1
        self.counters[kind] = n
        return str(uuid.uuid5(NS, f"{self.name}/{kind}/{n}"))

    def next_ref(self, prefix: str) -> str:
        n = self.counters.get("ref" + prefix, 0) + 1
        self.counters["ref" + prefix] = n
        return f"{prefix}{n}"

    def bump(self, key: str, n: int = 1) -> None:
        self.gen_counts[key] = self.gen_counts.get(key, 0) + n

    @property
    def ncu(self) -> int:
        return self.stk.ncu

    def L(self, i: int) -> str:
        """Source layer name of 1-based copper index ``i`` (top = 1)."""
        return cu_names(self.ncu)[i - 1]

    def net(self, name: str) -> str:
        if name and name not in self.nets:
            self.nets[name] = len(self.nets)
        return name

    def _netn(self, name: str) -> list:
        """Item-level net reference in the board's syntax flavour."""
        if not name:
            return []
        self.net(name)
        if self.kicad >= 10:
            return [["net", Q(name)]]
        return [["net", self.nets[name]]]

    def _padnet(self, name: str) -> list:
        if not name:
            return []
        self.net(name)
        if self.kicad >= 10:
            return [["net", Q(name)]]
        return [["net", self.nets[name], Q(name)]]

    # ---- primitives ----------------------------------------------------
    def seg(self, li: int, net: str, w: float, p0, p1) -> float:
        self.body.append(["segment", ["start", r6(p0[0]), r6(p0[1])], ["end", r6(p1[0]), r6(p1[1])],
                          ["width", w], ["layer", Q(self.L(li))], *self._netn(net), ["uuid", Q(self.uid("seg"))]])
        self.bump("tracks")
        return math.hypot(p1[0] - p0[0], p1[1] - p0[1])

    def track(self, li: int, net: str, w: float, pts) -> float:
        total = 0.0
        for a, b in zip(pts[:-1], pts[1:]):
            total += self.seg(li, net, w, a, b)
        return total

    def via(self, x: float, y: float, net: str, size: float = 0.5, drill: float = 0.25,
            span: tuple[int, int] | None = None, kind: str | None = None,
            bd_top: tuple[float, int] | None = None, bd_bottom: tuple[float, int] | None = None) -> None:
        """Add a via. ``span`` = (first, last) copper index (default: through).

        kind: None (through) / 'blind' / 'buried' / 'micro'.
        ``bd_bottom`` = (size, end_layer_index) -> tertiary_drill from B.Cu to that layer (end layer is cut);
        ``bd_top`` likewise -> top backdrill (KiCad 10.0.5 writes this as ``(backdrill ...)``).
        """
        a, b = span or (1, self.ncu)
        node: list = ["via"]
        if kind:
            node.append(kind)
        node += [["at", r6(x), r6(y)], ["size", size], ["drill", drill]]
        if bd_top:
            node.append(["backdrill", ["size", bd_top[0]], ["layers", Q("F.Cu"), Q(self.L(bd_top[1]))]])
        if bd_bottom:
            node.append(["tertiary_drill", ["size", bd_bottom[0]], ["layers", Q("B.Cu"), Q(self.L(bd_bottom[1]))]])
        node.append(["layers", Q(self.L(a)), Q(self.L(b))])
        node += self._netn(net)
        node.append(["uuid", Q(self.uid("via"))])
        self.body.append(node)
        self.bump("via_" + (kind or "through"))

    def footprint(self, ref: str, x: float, y: float, pads: list[dict], side: int = 1,
                  libid: str = "OA_Test:Pad", value: str = "") -> None:
        """side=1 top (F.Cu), side=-1 bottom (B.Cu).  Pad offsets are footprint-relative."""
        fl = "F.Cu" if side == 1 else "B.Cu"
        silk, fab = ("F.SilkS", "F.Fab") if side == 1 else ("B.SilkS", "B.Fab")
        tht = any(p["type"] == "thru_hole" for p in pads)

        def prop(k: str, v: str, layer: str) -> list:
            return ["property", Q(k), Q(v), ["at", 0, 0, 0], ["layer", Q(layer)], ["hide", "yes"],
                    ["uuid", Q(self.uid("prop"))], ["effects", ["font", ["size", 1.27, 1.27]]]]

        node: list = ["footprint", Q(libid), ["layer", Q(fl)], ["uuid", Q(self.uid("fp"))], ["at", r6(x), r6(y)],
                      prop("Reference", ref, silk), prop("Value", value or libid, fab),
                      prop("Datasheet", "", fab), prop("Description", "", fab),
                      ["attr", "through_hole" if tht else "smd"]]
        if self.kicad >= 10:
            node.append(["duplicate_pad_numbers_are_jumpers", "no"])
        for p in pads:
            if p["type"] == "smd":
                layers = ["F.Cu", "F.Mask", "F.Paste"] if side == 1 else ["B.Cu", "B.Mask", "B.Paste"]
            else:
                layers = ["*.Cu", "*.Mask"]
            pn: list = ["pad", Q(str(p["n"])), p["type"], p.get("shape", "rect"),
                        ["at", r6(p.get("dx", 0)), r6(p.get("dy", 0))], ["size", p["w"], p.get("h", p["w"])]]
            if p["type"] == "thru_hole":
                pn.append(["drill", p["drill"]])
            pn.append(["layers", *[Q(s) for s in layers]])
            if p["type"] == "thru_hole":
                pn.append(["remove_unused_layers", "no"])
            pn += self._padnet(p.get("net", ""))
            pn.append(["thermal_bridge_angle", 45])
            pn.append(["uuid", Q(self.uid("pad"))])
            node.append(pn)
            self.bump("pads")
        node.append(["embedded_fonts", "no"])
        self.body.append(node)
        self.bump("footprints")

    def tp(self, x: float, y: float, net: str, li: int = 1, w: float = 0.6, h: float | None = None,
           prefix: str = "TP") -> str:
        ref = self.next_ref(prefix)
        side = 1 if li == 1 else -1
        assert li in (1, self.ncu)
        self.footprint(ref, x, y, [{"n": 1, "type": "smd", "shape": "rect", "w": w, "h": h or w, "net": net}], side=side)
        return ref

    def tht_tp(self, x: float, y: float, net: str, size: float = 0.9, drill: float = 0.4, prefix: str = "TH") -> str:
        ref = self.next_ref(prefix)
        self.footprint(ref, x, y, [{"n": 1, "type": "thru_hole", "shape": "circle", "w": size, "drill": drill,
                                    "net": net}])
        return ref

    def zone(self, net: str, li: int, pts, priority: int = 0, clearance: float = 0.3, min_thk: float = 0.25,
             keepout_pour: bool = False, connect: bool = True) -> None:
        """Copper zone (net given) or a copper-pour keepout rule area (``keepout_pour=True``)."""
        node: list = ["zone"]
        if keepout_pour:
            node += [["layer", Q(self.L(li))]]
        else:
            if self.kicad >= 10:
                node += [["net", Q(self.net(net))], ["layer", Q(self.L(li))]]
            else:
                node += [["net", self.nets[self.net(net)]], ["net_name", Q(net)], ["layer", Q(self.L(li))]]
        node += [["uuid", Q(self.uid("zone"))], ["hatch", "edge", 0.5]]
        if priority:
            node.append(["priority", priority])
        node.append(["connect_pads", *(["yes"] if connect and not keepout_pour else []), ["clearance", clearance]])
        node.append(["min_thickness", min_thk])
        if keepout_pour:
            node.append(["keepout", ["tracks", "allowed"], ["vias", "allowed"], ["pads", "allowed"],
                         ["copperpour", "not_allowed"], ["footprints", "allowed"]])
        node.append(["fill", ["thermal_gap", 0.5], ["thermal_bridge_width", 0.5]])
        node.append(["polygon", ["pts", *[["xy", r6(x), r6(y)] for x, y in pts]]])
        self.body.append(node)
        self.bump("rule_areas" if keepout_pour else "zones")

    def edge_rect(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self.body.append(["gr_rect", ["start", x0, y0], ["end", x1, y1], ["stroke", ["width", 0.1], ["type", "default"]],
                          ["fill", "no"], ["layer", Q("Edge.Cuts")], ["uuid", Q(self.uid("gr"))]])

    # ---- output --------------------------------------------------------
    def nodes(self, fills: dict[str, list[list]] | None = None) -> list:
        v10 = self.kicad >= 10
        root: list = ["kicad_pcb", ["version", 20260206 if v10 else 20241229], ["generator", Q("pcbnew")],
                      ["generator_version", Q("10.0" if v10 else "9.0")],
                      ["general", ["thickness", self.stk.total_nm() / 1e6], ["legacy_teardrops", "no"]],
                      ["paper", Q("A3")]]
        ids = cu_ids(self.ncu)
        lay: list = ["layers"]
        for n in cu_names(self.ncu):
            lay.append([ids[n], Q(n), "signal"])
        for i, n, t, alias in TECH_LAYERS:
            lay.append([i, Q(n), t] + ([Q(alias)] if alias else []))
        root.append(lay)
        root.append(["setup", self.stk.sexpr(), ["pad_to_mask_clearance", 0],
                     ["allow_soldermask_bridges_in_footprints", "no"]])
        if not v10:
            for n, i in self.nets.items():
                root.append(["net", i, Q(n)])
        root.extend(self.body)
        root.append(["embedded_fonts", "no"])
        return root

    def pcb_text(self, fills: dict[str, str] | None = None) -> str:
        """Serialise.  ``fills`` maps zone uuid -> raw text of ``(filled_polygon ...)`` blocks to inject (v9 flavour)."""
        text = fmt(self.nodes()) + "\n"
        if fills:
            for zid, blocks in fills.items():
                marker = f'(uuid "{zid}")'
                i = text.index(marker)
                j = text.index("\n\t)\n", i)  # end of the zone block
                text = text[:j] + "\n" + blocks + text[j:]
        return text

    def pro_json(self) -> str:
        classes = [{
            "bus_width": 12, "clearance": 0.1, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
            "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.3, "microvia_drill": 0.1,
            "name": "Default", "pcb_color": "rgba(0, 0, 0, 0.000)", "priority": 2147483647,
            "schematic_color": "rgba(0, 0, 0, 0.000)", "track_width": 0.2, "via_diameter": 0.5,
            "via_drill": 0.25, "wire_width": 6}]
        for k, nc in enumerate(self.netclasses):
            d = {"bus_width": 12, "line_style": 0, "name": nc["name"], "pcb_color": "rgba(0, 0, 0, 0.000)",
                 "priority": k, "schematic_color": "rgba(0, 0, 0, 0.000)", "wire_width": 6}
            for key in ("track_width", "clearance", "diff_pair_width", "diff_pair_gap", "via_diameter", "via_drill"):
                if key in nc:
                    d[key] = nc[key]
            classes.append(d)
        rules = {"max_error": 0.005, "min_clearance": 0.1, "min_connection": 0.0, "min_copper_edge_clearance": 0.1,
                 "min_hole_clearance": 0.25, "min_hole_to_hole": 0.25, "min_microvia_diameter": 0.2,
                 "min_microvia_drill": 0.1, "min_resolved_spokes": 1, "min_through_hole_diameter": 0.2,
                 "min_track_width": 0.1, "min_via_annular_width": 0.1, "min_via_diameter": 0.4,
                 "solder_mask_to_copper_clearance": 0.0, "use_height_for_length_calcs": True}
        rules.update(self.rules)
        pro = {
            "board": {"design_settings": {"rules": rules, "rule_severities": {
                "silk_over_copper": "ignore", "silk_overlap": "ignore", "silk_edge_clearance": "ignore",
                "missing_courtyard": "ignore", "footprint_type_mismatch": "ignore", "lib_footprint_issues": "ignore",
                "lib_footprint_mismatch": "ignore", "text_height": "ignore", "text_thickness": "ignore",
                "track_dangling": "warning", "via_dangling": "warning"}}},
            "meta": {"filename": "board.kicad_pro", "version": 3},
            "net_settings": {"classes": classes, "meta": {"version": 4}, "net_colors": None,
                             "netclass_assignments": None,
                             "netclass_patterns": [{"netclass": c, "pattern": p} for c, p in self.patterns]},
            "sheets": [], "text_variables": {},
        }
        return json.dumps(pro, indent=2, sort_keys=True) + "\n"
