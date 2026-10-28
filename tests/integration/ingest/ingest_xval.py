# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="union-attr,arg-type,import-untyped"
"""Cross-reader comparison harness (WP-208).

Compares the ``Board`` read natively from a ``.kicad_pcb`` with the ``Board`` read from the
IPC-2581 export of the same file, object by object, and checks both against ``expected.yaml``.

Every comparison yields a :class:`Finding`. A finding that is out of tolerance is ``fail`` unless an
entry of ``tests/golden/ingest_cross/exceptions.toml`` matches it; it is then ``whitelisted`` and
carries the entry's cause. Nothing here imports ``pcbnew``.
"""

from __future__ import annotations

import csv
import fnmatch
import io
import json
import math
import tomllib
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from shapely.geometry import Polygon as ShPolygon
from shapely.ops import unary_union

from oa_geometry import BoardGeometry
from oa_ingest_ipc2581 import Ipc2581Reader
from oa_ingest_kicad import KiCadReader
from oa_kicad_bridge import KiCadTools, export_ipc2581
from oa_model import Board
from oa_model.io import dumps_board
from oa_model.setup import ReadOptions

REPO = Path(__file__).resolve().parents[3]
SYNTHETIC = REPO / "tests" / "boards" / "kicad10"
EXCEPTIONS_TOML = REPO / "tests" / "golden" / "ingest_cross" / "exceptions.toml"

# ---- tolerances (TASKS.md WP-208 acceptance)
AREA_REL_NATIVE_EXPECTED = 1e-3  # native reader vs expected.yaml copper area
AREA_REL_CROSS = 5e-3  # native vs IPC-2581 copper area
AREA_ABS_FLOOR = 1e-9  # m^2: below this the relative error of tiny nets is not meaningful
Z_ABS_NATIVE = 1e-9  # m, native z vs expected.yaml
Z_ABS_CROSS = 1e-6  # m, native z vs IPC-2581
POS_ABS = 1e-6  # m, pad / via / component positions
OUTLINE_REL = 1e-3
LENGTH_REL = 1e-4

STATUSES = ("pass", "whitelisted", "fail")


def tolerances() -> dict[str, float]:
    """The tolerances the findings are judged with (for the report header)."""
    return {
        "area_rel_native_vs_expected": AREA_REL_NATIVE_EXPECTED,
        "area_rel_native_vs_ipc2581": AREA_REL_CROSS,
        "area_abs_floor_m2": AREA_ABS_FLOOR,
        "z_abs_native_vs_expected_m": Z_ABS_NATIVE,
        "z_abs_native_vs_ipc2581_m": Z_ABS_CROSS,
        "position_abs_m": POS_ABS,
        "outline_area_rel": OUTLINE_REL,
        "track_length_rel": LENGTH_REL,
    }


@dataclass(frozen=True)
class Finding:
    """One comparison. ``native`` and ``other`` are printable values; ``delta`` is numeric."""

    board: str
    reader: str  # "ipc2581" (native vs IPC) or "kicad" / "ipc2581" for expected.yaml checks
    source: str  # "cross" or "expected"
    check: str
    key: str
    native: str
    other: str
    delta: float | None
    tol: float | None
    status: str
    exception: str = ""
    cause: str = ""
    detail: str = ""

    def row(self) -> dict[str, Any]:
        return {
            "board": self.board,
            "source": self.source,
            "reader": self.reader,
            "check": self.check,
            "key": self.key,
            "native": self.native,
            "other": self.other,
            "delta": "" if self.delta is None else f"{self.delta:.6g}",
            "tol": "" if self.tol is None else f"{self.tol:.6g}",
            "status": self.status,
            "exception": self.exception,
            "cause": self.cause,
            "detail": self.detail,
        }


CSV_FIELDS = (
    "board",
    "source",
    "reader",
    "check",
    "key",
    "native",
    "other",
    "delta",
    "tol",
    "status",
    "exception",
    "cause",
    "detail",
)


def findings_to_csv(findings: Iterable[Finding]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, lineterminator="\n")
    w.writeheader()
    for f in findings:
        w.writerow(f.row())
    return buf.getvalue()


# ---------------------------------------------------------------------------- exceptions
@dataclass(frozen=True)
class Exc:
    id: str
    checks: tuple[str, ...]
    boards: tuple[str, ...]
    keys: tuple[str, ...]
    sources: tuple[str, ...]
    readers: tuple[str, ...]
    cause: str
    evidence: str
    scope: str  # "synthetic" or "demo": where the entry is expected to be used

    def matches(self, board: str, source: str, reader: str, check: str, key: str) -> bool:
        return (
            any(fnmatch.fnmatchcase(check, c) for c in self.checks)
            and any(fnmatch.fnmatchcase(board, b) for b in self.boards)
            and any(fnmatch.fnmatchcase(key, k) for k in self.keys)
            and source in self.sources
            and reader in self.readers
        )


def load_exceptions(path: Path = EXCEPTIONS_TOML) -> list[Exc]:
    doc = tomllib.loads(path.read_text(encoding="utf-8"))
    out: list[Exc] = []
    for e in doc.get("exception", []):
        out.append(
            Exc(
                id=e["id"],
                checks=(e["check"],) if isinstance(e["check"], str) else tuple(e["check"]),
                boards=tuple(e.get("boards", ["*"])),
                keys=tuple(e.get("keys", ["*"])),
                sources=tuple(e.get("sources", ["cross"])),
                readers=tuple(e.get("readers", ["ipc2581"])),
                cause=e["cause"],
                evidence=e["evidence"],
                scope=e.get("scope", "synthetic"),
            )
        )
    return out


class Judge:
    """Builds findings; out-of-tolerance ones are matched against the exception list."""

    def __init__(
        self, board: str, source: str, reader: str, exceptions: Sequence[Exc] | None = None
    ) -> None:
        self.board = board
        self.source = source
        self.reader = reader
        self.exceptions = list(load_exceptions() if exceptions is None else exceptions)
        self.findings: list[Finding] = []
        self.used: set[str] = set()

    def add(
        self,
        check: str,
        key: str,
        native: Any,
        other: Any,
        *,
        ok: bool,
        delta: float | None = None,
        tol: float | None = None,
        detail: str = "",
    ) -> None:
        status, exc, cause = "pass", "", ""
        if not ok:
            status = "fail"
            for e in self.exceptions:
                if e.matches(self.board, self.source, self.reader, check, key):
                    status, exc, cause = "whitelisted", e.id, e.cause
                    self.used.add(e.id)
                    break
        self.findings.append(
            Finding(
                self.board,
                self.reader,
                self.source,
                check,
                key,
                _fmt(native),
                _fmt(other),
                delta,
                tol,
                status,
                exc,
                cause,
                detail,
            )
        )

    def equal(self, check: str, key: str, native: Any, other: Any) -> None:
        self.add(check, key, native, other, ok=native == other)

    def close(
        self,
        check: str,
        key: str,
        native: float,
        other: float,
        *,
        abs_tol: float = 0.0,
        rel_tol: float = 0.0,
        abs_floor: float = 0.0,
    ) -> None:
        """|a-b| <= abs_tol + rel_tol*max(|a|,|b|); differences below ``abs_floor`` pass."""
        d = abs(native - other)
        scale = max(abs(native), abs(other))
        ok = d <= max(abs_tol + rel_tol * scale, abs_floor)
        rel = d / scale if scale else 0.0
        tol = rel_tol if rel_tol and not abs_tol else abs_tol
        self.add(check, key, native, other, ok=ok, delta=rel if rel_tol else d, tol=tol)


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.9g}"
    if isinstance(v, (list, tuple, set, frozenset)):
        return ";".join(sorted(str(x) for x in v)) if isinstance(v, (set, frozenset)) else str(v)
    return str(v)


# ---------------------------------------------------------------------------- readers
def read_native(path: Path) -> Board:
    return KiCadReader().read(path, ReadOptions())


def read_ipc(path: Path) -> Board:
    return Ipc2581Reader().read(path, ReadOptions())


def _poly(p: Any) -> ShPolygon:
    g = ShPolygon(np.asarray(p.exterior.array), [np.asarray(h.array) for h in p.holes])
    return g if g.is_valid else g.buffer(0)


def kind_folded(kind: str) -> str:
    """IPC-2581 has no micro-via flag: micro vias come back as blind."""
    return "blind" if kind == "micro" else kind


def via_kind_counts(b: Board) -> dict[str, int]:
    c = {"through": 0, "blind": 0, "buried": 0}
    for v in b.vias:
        c[kind_folded(v.kind)] += 1
    return c


class AreaCache:
    """Per-layer per-net copper area of a board through ``oa_geometry`` (the production path)."""

    def __init__(self, board: Board) -> None:
        self.board = board
        self.geo = BoardGeometry.build(board, arc_tol=1e-6)
        self.cu = [layer.id for layer in board.stackup.layers if layer.kind == "conductor"]
        self.area: dict[str, dict[str, float]] = {}
        for lid in self.cu:
            self.area[lid] = {
                n: float(g.area) for n, g in self.geo.copper_by_net(lid).items() if n is not None
            }


def conductor_layers(b: Board) -> list[Any]:
    return [layer for layer in b.stackup.layers if layer.kind == "conductor"]


def dielectric_dkdf(b: Board, layer: Any) -> tuple[float, float]:
    mats = {m.name: m for m in b.stackup.materials}
    m = mats[layer.material].model
    return float(m.dk), float(m.df)


# ---------------------------------------------------------------------------- cross compare
def compare_cross(
    name: str,
    native: Board,
    ipc: Board,
    *,
    native_areas: AreaCache | None = None,
    ipc_areas: AreaCache | None = None,
    exceptions: Sequence[Exc] | None = None,
) -> Judge:
    """Native vs IPC-2581, object by object."""
    j = Judge(name, "cross", "ipc2581", exceptions)
    _cmp_nets(j, native, ipc)
    _cmp_components(j, native, ipc)
    _cmp_pads(j, native, ipc)
    _cmp_vias(j, native, ipc)
    _cmp_tracks(j, native, ipc)
    _cmp_zones(j, native, ipc)
    _cmp_stackup(j, native, ipc)
    _cmp_outline(j, native, ipc)
    _cmp_area(j, native_areas or AreaCache(native), ipc_areas or AreaCache(ipc))
    return j


def _cmp_nets(j: Judge, a: Board, b: Board) -> None:
    na, nb = {n.id for n in a.nets}, {n.id for n in b.nets}
    j.equal("nets.count", "all", len(na), len(nb))
    for n in sorted(na - nb):
        j.add("nets.set", n, "present", "missing", ok=False)
    for n in sorted(nb - na):
        j.add("nets.set", n, "missing", "present", ok=False)
    if na == nb:
        j.add("nets.set", "all", len(na), len(nb), ok=True)


def _cmp_components(j: Judge, a: Board, b: Board) -> None:
    j.equal("components.count", "all", len(a.components), len(b.components))
    pads_a = {p.id: p for p in a.pads}
    pads_b = {p.id: p for p in b.pads}

    has_a, has_b = _has_copper(a), _has_copper(b)

    def own(c: Any, pads: dict[str, Any], has: Callable[[Any], bool]) -> int:
        # non-plated holes are listed by the IPC export at board level, not under the component;
        # pads without copper or drill (apertures) are not exported at all
        return sum(1 for i in c.pads if pads[i].kind != "npth" and has(pads[i]))

    cb = {c.refdes: c for c in b.components}
    for c in a.components:
        o = cb.get(c.refdes)
        if o is None:
            j.add("components.match", c.refdes, "present", "missing", ok=False)
            continue
        d = math.dist(c.position, o.position)
        j.close("components.position", c.refdes, 0.0, d, abs_tol=POS_ABS)
        j.equal("components.side", c.refdes, c.side, o.side)
        dr = abs((c.rotation - o.rotation + math.pi) % (2 * math.pi) - math.pi)
        j.close("components.rotation", c.refdes, 0.0, dr, abs_tol=1e-6)
        j.equal("components.pad_count", c.refdes, own(c, pads_a, has_a), own(o, pads_b, has_b))


def _has_copper(b: Board) -> Callable[[Any], bool]:
    stacks = {ps.id: ps for ps in b.padstacks}

    def has(p: Any) -> bool:
        ps = stacks[p.padstack]
        return ps.drill is not None or any(layer.pad is not None for layer in ps.layers)

    return has


def _pad_kind(p: Any) -> str:
    # IPC-2581 has no edge-connector flag: such pads are plain SMD pads
    return "smd" if p.kind in ("connector_edge", "aperture") else str(p.kind)


def _pair_pads(xs: list[Any], ys: list[Any]) -> tuple[list[tuple[Any, Any]], list[Any], list[Any]]:
    """Pair pads by position (<= POS_ABS), preferring the same refdes and number, then net."""
    cells: dict[tuple[int, int], list[Any]] = {}
    for y in ys:
        cells.setdefault((round(y.position[0] * 1e5), round(y.position[1] * 1e5)), []).append(y)
    taken: set[str] = set()
    pairs: list[tuple[Any, Any]] = []
    left: list[Any] = []
    for x in sorted(xs, key=lambda p: (p.component or "", p.number, p.position)):
        cx, cy = round(x.position[0] * 1e5), round(x.position[1] * 1e5)
        cand = [
            y
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            for y in cells.get((cx + dx, cy + dy), [])
            if y.id not in taken and math.dist(x.position, y.position) <= POS_ABS
        ]
        if not cand:
            left.append(x)
            continue
        best = min(
            cand,
            key=lambda y: (
                (y.component or "", y.number) != (x.component or "", x.number),
                y.net != x.net,
                math.dist(x.position, y.position),
            ),
        )
        taken.add(best.id)
        pairs.append((x, best))
    return pairs, left, [y for y in ys if y.id not in taken]


def _examples(items: list[str], n: int = 4) -> str:
    return ", ".join(items[:n]) + (f", ... ({len(items)})" if len(items) > n else "")


def _cmp_pads(j: Judge, a: Board, b: Board) -> None:
    has_a, has_b = _has_copper(a), _has_copper(b)
    pa = [p for p in a.pads if has_a(p)]
    pb = [p for p in b.pads if has_b(p)]
    # pads without copper and drill (paste/mask-only apertures) are not exported to IPC-2581
    ap_a, ap_b = len(a.pads) - len(pa), len(b.pads) - len(pb)
    j.equal("pads.apertures", "no_copper_no_drill", ap_a, ap_b)
    j.equal("pads.count", "with_copper", len(pa), len(pb))
    for kind in ("smd", "through_hole", "npth"):
        j.equal(
            "pads.count_by_kind",
            kind,
            sum(_pad_kind(p) == kind for p in pa),
            sum(_pad_kind(p) == kind for p in pb),
        )
    pairs, left_a, left_b = _pair_pads(pa, pb)
    j.add(
        "pads.unmatched",
        "native_only",
        len(left_a),
        0,
        ok=not left_a,
        delta=float(len(left_a)),
        detail=_examples([f"{p.component}.{p.number}" for p in left_a]),
    )
    j.add(
        "pads.unmatched",
        "ipc_only",
        0,
        len(left_b),
        ok=not left_b,
        delta=float(len(left_b)),
        detail=_examples([f"{p.component}.{p.number}" for p in left_b]),
    )
    worst = max((math.dist(x.position, y.position) for x, y in pairs), default=0.0)
    j.close("pads.position", "max", 0.0, worst, abs_tol=POS_ABS)
    npth = [(x, y) for x, y in pairs if x.kind == "npth"]
    plain = [(x, y) for x, y in pairs if x.kind != "npth"]
    for what, fn in (
        ("net", lambda p: p.net),
        ("component", lambda p: p.component),
        ("number", lambda p: p.number),
        ("kind", _pad_kind),
    ):
        bad = [f"{x.component}.{x.number}:{fn(x)}/{fn(y)}" for x, y in plain if fn(x) != fn(y)]
        j.add(f"pads.{what}", "mismatches", len(bad), 0, ok=not bad, detail=_examples(bad))
    # non-plated holes: the IPC export lists them at board level, without the footprint
    bad = [f"{x.id}:{x.component}/{y.component}" for x, y in npth if x.component != y.component]
    j.add("pads.npth_component", "mismatches", len(bad), 0, ok=not bad, detail=_examples(bad))
    # copper layers per pad (merged stacks and mask/paste apertures show up here)
    sa = {ps.id: ps for ps in a.padstacks}
    sb = {ps.id: ps for ps in b.padstacks}

    def layers(board_stacks: dict[str, Any], p: Any) -> tuple[str, ...]:
        return tuple(sorted(lay.layer for lay in board_stacks[p.padstack].layers if lay.pad))

    bad = [
        f"{x.component}.{x.number}:{layers(sa, x)}/{layers(sb, y)}"
        for x, y in pairs
        if x.kind != "npth" and layers(sa, x) != layers(sb, y)
    ]
    j.add("pads.layers", "mismatches", len(bad), 0, ok=not bad, detail=_examples(bad))


def _via_key(v: Any) -> tuple[int, int, str]:
    return (round(v.position[0] * 1e6), round(v.position[1] * 1e6), v.net or "")


def _cmp_vias(j: Judge, a: Board, b: Board) -> None:
    ca, cb = via_kind_counts(a), via_kind_counts(b)
    for k in ca:
        j.equal("vias.count", k, ca[k], cb[k])
    j.equal("vias.micro_flag", "native_micro_vias", sum(v.kind == "micro" for v in a.vias), 0)
    ib = {_via_key(v): v for v in b.vias}
    unmatched = 0
    for v in a.vias:
        o = ib.get(_via_key(v))
        if o is None:
            unmatched += 1
            continue
        j.equal("vias.span", v.id, (v.start_layer, v.end_layer), (o.start_layer, o.end_layer))
        j.equal("vias.kind", v.id, kind_folded(v.kind), kind_folded(o.kind))
        for bd in v.backdrills:
            ob = next((x for x in o.backdrills if x.side == bd.side), None)
            if ob is None:
                j.add("vias.backdrill", f"{v.id}.{bd.side}", "present", "missing", ok=False)
                continue
            j.equal(
                "vias.backdrill.layers",
                f"{v.id}.{bd.side}",
                (bd.last_removed_layer, bd.must_not_cut),
                (ob.last_removed_layer, ob.must_not_cut),
            )
            j.close(
                "vias.backdrill.diameter",
                f"{v.id}.{bd.side}",
                bd.diameter,
                ob.diameter,
                abs_tol=1e-9,
            )
        if len(v.backdrills) != len(o.backdrills):
            j.add(
                "vias.backdrill",
                f"{v.id}.sides",
                len(v.backdrills),
                len(o.backdrills),
                ok=False,
            )
    j.add("vias.match", "unmatched", unmatched, 0, ok=unmatched == 0, delta=float(unmatched))


def _track_len(t: Any) -> float:
    total = 0.0
    for s in t.path:
        if s.kind == "arc":
            r = math.dist(s.start, s.center)
            a0 = math.atan2(s.start[1] - s.center[1], s.start[0] - s.center[0])
            a1 = math.atan2(s.end[1] - s.center[1], s.end[0] - s.center[0])
            d = (a1 - a0) % (2 * math.pi) if s.ccw else (a0 - a1) % (2 * math.pi)
            total += r * (d if d else 2 * math.pi)
        else:
            total += math.dist(s.start, s.end)
    return total


def _cmp_tracks(j: Judge, a: Board, b: Board) -> None:
    j.equal("tracks.count", "all", len(a.tracks), len(b.tracks))
    la: dict[tuple[str, str], float] = {}
    lb: dict[tuple[str, str], float] = {}
    for src, dst in ((a, la), (b, lb)):
        for t in src.tracks:
            k = (t.layer, t.net or "")
            dst[k] = dst.get(k, 0.0) + _track_len(t)
    worst = 0.0
    for k in sorted(set(la) | set(lb)):
        x, y = la.get(k, 0.0), lb.get(k, 0.0)
        scale = max(x, y)
        worst = max(worst, abs(x - y) / scale if scale else 0.0)
    j.close("tracks.length", "worst_layer_net", 0.0, worst, rel_tol=0.0, abs_tol=LENGTH_REL)
    w_a = sorted(round(t.width * 1e9) for t in a.tracks)
    w_b = sorted(round(t.width * 1e9) for t in b.tracks)
    j.equal("tracks.widths", "all", w_a == w_b, True)


def _net_layer_zones(b: Board) -> dict[tuple[str, str], list[Any]]:
    """Copper zones keyed by (net, layer): a KiCad zone may span layers, IPC-2581 never does."""
    out: dict[tuple[str, str], list[Any]] = {}
    for z in b.zones:
        if z.keepout is not None:
            continue
        for f in z.fills:
            if f.polygons:
                out.setdefault((z.net or "", f.layer), []).append(z)
    return out


def _cmp_zones(j: Judge, a: Board, b: Board) -> None:
    # IPC-2581 has no zone object: zones come back as filled contours per (net, layer)
    za, zb = _net_layer_zones(a), _net_layer_zones(b)
    j.equal("zones.count", "net_layer_pairs", len(za), len(zb))
    j.equal("zones.rule_areas", "all", sum(z.keepout is not None for z in a.zones), 0)
    j.equal(
        "zones.fill_state",
        "all",
        sorted({z.fill_state for zs in za.values() for z in zs}),
        ["filled"],
    )
    for (net, layer), zs in sorted(za.items()):
        key = f"{net}@{layer}"
        os_ = zb.get((net, layer))
        if os_ is None:
            j.add("zones.match", key, "present", "missing", ok=False)
            continue
        j.close(
            "zones.outline_area",
            key,
            unary_union([_poly(z.outline) for z in zs]).area,
            unary_union([_poly(z.outline) for z in os_]).area,
            rel_tol=1e-6,
        )
        j.equal(
            "zones.priority", key, sorted(z.priority for z in zs), sorted(z.priority for z in os_)
        )


def _cmp_stackup(j: Judge, a: Board, b: Board) -> None:
    ia = {layer.id: layer for layer in a.stackup.layers}
    ib = {layer.id: layer for layer in b.stackup.layers}
    for lid in sorted(set(ia) - set(ib)):
        j.add("stackup.layer_set", lid, "present", "missing", ok=False)
    for lid in sorted(set(ib) - set(ia)):
        j.add("stackup.layer_set", lid, "missing", "present", ok=False)
    common = [layer.id for layer in a.stackup.layers if layer.id in ib]
    j.add("stackup.layer_set", "common", len(common), len(common), ok=True)
    # z origin: KiCad's IPC export always writes a bottom solder-mask layer
    low = conductor_layers(a)[-1].id
    off = ib[low].z_bottom - ia[low].z_bottom
    j.close("stackup.z_offset", "bottom_origin", 0.0, off, abs_tol=Z_ABS_CROSS)
    for lid in common:
        la, lb = ia[lid], ib[lid]
        j.close("stackup.thickness", lid, la.thickness, lb.thickness, abs_tol=1e-9)
        j.close("stackup.z_bottom", lid, la.z_bottom, lb.z_bottom - off, abs_tol=Z_ABS_CROSS)
        j.close("stackup.z_top", lid, la.z_top, lb.z_top - off, abs_tol=Z_ABS_CROSS)
        if la.kind == "dielectric" and lb.kind == "dielectric":
            da, db = dielectric_dkdf(a, la), dielectric_dkdf(b, lb)
            j.equal("stackup.dk", lid, da[0], db[0])
            j.equal("stackup.df", lid, da[1], db[1])
    j.equal("stackup.conductor_count", "all", len(conductor_layers(a)), len(conductor_layers(b)))


def _cmp_outline(j: Judge, a: Board, b: Board) -> None:
    aa, ab = _poly(a.outline.polygon).area, _poly(b.outline.polygon).area
    j.close("outline.area", "all", aa, ab, rel_tol=OUTLINE_REL)
    j.equal("outline.cutouts", "all", len(a.outline.polygon.holes), len(b.outline.polygon.holes))


def _cmp_area(j: Judge, a: AreaCache, b: AreaCache) -> None:
    for lid in a.cu:
        nets = sorted(set(a.area.get(lid, {})) | set(b.area.get(lid, {})))
        for n in nets:
            j.close(
                "copper_area",
                f"{lid}/{n}",
                a.area[lid].get(n, 0.0),
                b.area.get(lid, {}).get(n, 0.0),
                rel_tol=AREA_REL_CROSS,
                abs_floor=AREA_ABS_FLOOR,
            )


# ---------------------------------------------------------------------------- expected.yaml
def load_expected(board_dir: Path) -> dict[str, Any]:
    doc = yaml.safe_load((board_dir / "expected.yaml").read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    return doc


def compare_expected(
    name: str,
    reader: str,
    board: Board,
    exp: Mapping[str, Any],
    *,
    areas: AreaCache | None = None,
    exceptions: Sequence[Exc] | None = None,
) -> Judge:
    """Counts, stackup z and per-net copper area against ``expected.yaml``.

    ``reader`` is ``kicad`` (exact counts, 1 nm z, 0.1 % area) or ``ipc2581`` (micro vias fold into
    blind, z shifted by the forced bottom mask and checked to 1 um, area 0.5 %).
    """
    j = Judge(name, "expected", reader, exceptions)
    ipc = reader == "ipc2581"
    c = exp["counts"]
    ev = c.get("vias", {})
    j.equal("counts.nets", "all", len(board.nets), c["nets"])
    j.equal("counts.pads", "all", len(board.pads), c["pads"])
    j.equal("counts.pads_smd", "all", sum(p.kind == "smd" for p in board.pads), c["pads_smd"])
    j.equal(
        "counts.pads_tht",
        "all",
        sum(p.kind == "through_hole" for p in board.pads),
        c["pads_tht"],
    )
    j.equal("counts.tracks", "all", len(board.tracks), c["tracks"] + c.get("arcs", 0))
    j.equal("counts.zones", "all", sum(z.keepout is None for z in board.zones), c["zones"])
    if not ipc:
        j.equal(
            "counts.rule_areas",
            "all",
            sum(z.keepout is not None for z in board.zones),
            c.get("rule_areas", 0),
        )
    j.equal("counts.footprints", "all", len(board.components), c["footprints"])
    j.equal("counts.copper_layers", "all", len(conductor_layers(board)), c["copper_layers"])
    got = Counter(v.kind for v in board.vias)
    for k in ("through", "blind", "buried", "micro"):
        want = ev.get(k, 0)
        have = got.get(k, 0)
        if ipc:
            if k == "micro":
                continue
            if k == "blind":
                want += ev.get("micro", 0)
        j.equal(f"counts.vias.{k}", "all", have, want)

    by_id = {layer.id: layer for layer in board.stackup.layers}
    low = conductor_layers(board)[-1]
    off = low.z_bottom if ipc else 0.0
    ztol = Z_ABS_CROSS if ipc else Z_ABS_NATIVE
    if ipc:
        j.close("stackup.z_offset", "bottom_origin", 0.0, off, abs_tol=ztol)
    for sl in exp["stackup"]["layers"]:
        layer = by_id.get(sl["id"])
        if layer is None:
            j.add("stackup.layer_set", sl["id"], "missing", "present", ok=False)
            continue
        j.close("stackup.thickness", sl["id"], layer.thickness, sl["thickness"], abs_tol=ztol)
        j.close("stackup.z_bottom", sl["id"], layer.z_bottom - off, sl["z_bottom"], abs_tol=ztol)
        j.close("stackup.z_top", sl["id"], layer.z_top - off, sl["z_top"], abs_tol=ztol)
    mat = exp["materials"]
    for layer in board.stackup.layers:
        if layer.kind == "dielectric":
            dk, df = dielectric_dkdf(board, layer)
            j.equal("stackup.dk", layer.id, dk, mat["dk"])
            j.equal("stackup.df", layer.id, df, mat["df"])

    ac = areas or AreaCache(board)
    rel = AREA_REL_CROSS if ipc else AREA_REL_NATIVE_EXPECTED
    for lid, nets in exp["copper_area"]["layers"].items():
        for n, want_a in nets.items():
            j.close(
                "copper_area",
                f"{lid}/{n}",
                ac.area.get(lid, {}).get(n, 0.0),
                float(want_a),
                rel_tol=rel,
                abs_floor=AREA_ABS_FLOOR,
            )
    return j


# ---------------------------------------------------------------------------- canonical form
_ITEM_LISTS = ("pads", "vias", "tracks", "zones", "shapes", "padstacks")


def canonical_board(b: Board, *, drop: Sequence[str] = ()) -> dict[str, Any]:
    """Board content without item ids, source references, list order, ``meta`` and diagnostics.

    Two files with the same copper but different uuids (microstrip vs microstrip_v9) give equal
    canonical forms. Padstack references are replaced by the padstack content; ``drop`` names
    item fields to leave out (for example ``protection``).
    """
    doc = json.loads(dumps_board(b))
    doc.pop("meta", None)
    doc.pop("diagnostics", None)
    stacks = {p["id"]: {k: v for k, v in p.items() if k != "id"} for p in doc["padstacks"]}
    for key in _ITEM_LISTS:
        for item in doc[key]:
            item.pop("id", None)
            item.pop("source", None)
            if "padstack" in item:
                item["padstack"] = stacks[item["padstack"]]
            for d in drop:
                item.pop(d, None)
    for c in doc["components"]:
        c.pop("source", None)
        c["pads"] = len(c["pads"])
    for key in (*_ITEM_LISTS, "components"):
        doc[key] = sorted(doc[key], key=lambda it: json.dumps(it, sort_keys=True))
    for n in doc["nets"]:
        n.pop("source", None)
    return doc  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------- stale fills
def fill_outside_outline(board: Board, tol_area: float = 1e-9) -> list[tuple[str, float]]:
    """Zones whose fill extends beyond their own outline: the outline was edited after the fill.

    Returns ``(zone id, area outside [m^2])``. The KiCad reader's own ``INGEST.ZONE_STALE`` only
    sees fills overlapping foreign-net copper.
    """
    out: list[tuple[str, float]] = []
    for z in board.zones:
        if z.keepout is not None or not z.fills:
            continue
        outline = _poly(z.outline).buffer(1e-6)
        fill = unary_union([_poly(p) for f in z.fills for p in f.polygons])
        extra = float(fill.difference(outline).area)
        if extra > tol_area:
            out.append((z.id, extra))
    return out


# ---------------------------------------------------------------------------- summary / matrix
def status_counts(findings: Iterable[Finding]) -> dict[str, int]:
    c = Counter(f.status for f in findings)
    return {s: c.get(s, 0) for s in STATUSES}


def matrix_cell(findings: Sequence[Finding]) -> str:
    """'fail' if any fail, else 'whitelisted' if any whitelisted, else 'pass'."""
    s = status_counts(findings)
    return "fail" if s["fail"] else "whitelisted" if s["whitelisted"] else "pass"


@dataclass
class BoardResult:
    """Everything the report tool needs for one board."""

    name: str
    native: Board | None = None
    ipc: Board | None = None
    cross: Judge | None = None
    native_expected: Judge | None = None
    ipc_expected: Judge | None = None
    native_areas: AreaCache | None = None
    ipc_areas: AreaCache | None = None
    notes: list[str] = field(default_factory=list)

    def all_findings(self) -> list[Finding]:
        out: list[Finding] = []
        for j in (self.native_expected, self.ipc_expected, self.cross):
            if j is not None:
                out.extend(j.findings)
        return out


def run_board(
    name: str,
    kicad_pcb: Path,
    ipc_xml: Path | None,
    expected: Mapping[str, Any] | None,
    exceptions: Sequence[Exc] | None = None,
    progress: Callable[[str], None] | None = None,
) -> BoardResult:
    """Read both ways and run every comparison that has its inputs."""
    res = BoardResult(name)
    say = progress or (lambda _m: None)
    say(f"{name}: native")
    res.native = read_native(kicad_pcb)
    na = res.native_areas = AreaCache(res.native)
    if expected is not None:
        res.native_expected = compare_expected(
            name, "kicad", res.native, expected, areas=na, exceptions=exceptions
        )
    if ipc_xml is not None:
        say(f"{name}: ipc2581")
        res.ipc = read_ipc(ipc_xml)
        ia = res.ipc_areas = AreaCache(res.ipc)
        res.cross = compare_cross(
            name, res.native, res.ipc, native_areas=na, ipc_areas=ia, exceptions=exceptions
        )
        if expected is not None:
            res.ipc_expected = compare_expected(
                name, "ipc2581", res.ipc, expected, areas=ia, exceptions=exceptions
            )
    return res


# ---------------------------------------------------------------------------- KiCad demos
# (case name, demo directory, board file stem): the three demo boards of WP-208
DEMOS = (
    ("pic_programmer", "pic_programmer", "pic_programmer"),
    ("stickhub", "stickhub", "StickHub"),
    ("cm5_minima", "cm5_minima", "CM5_MINIMA_3"),
)


def kicad_tools() -> KiCadTools:
    return KiCadTools.discover()


def demo_board(directory: str, stem: str) -> Path | None:
    cli = kicad_tools().cli
    if cli is None:
        return None
    for share in (cli.parent.parent / "share" / "kicad", Path("/usr/share/kicad")):
        p = share / "demos" / directory / f"{stem}.kicad_pcb"
        if p.is_file():
            return p
    return None


def export_ipc(pcb: Path, out: Path) -> Path:
    """``kicad-cli pcb export ipc2581`` through the bridge."""
    return export_ipc2581(pcb, out, tools=kicad_tools())


def synthetic_boards() -> list[Path]:
    return sorted(p.parent for p in SYNTHETIC.glob("*/board.kicad_pcb"))
