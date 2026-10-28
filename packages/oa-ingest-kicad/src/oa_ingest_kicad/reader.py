# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``KiCadReader``: ``.kicad_pcb`` (format 20221018 .. 2026xxxx) to :class:`oa_model.Board`.

Conventions applied at the boundary (board-model.md section 2): mm to metres, the Y axis flipped
(``y_oa = -y_kicad``), angles to radians (counter-clockwise positive), copper layers renamed to
``L1..Ln`` from the PHYSICAL stack order (never from KiCad's layer ordinals, which were
renumbered in KiCad 9).

Backdrill end-layer semantics (verified with KiCad 10.0.5 pcbnew, see README and
``tools/verify_backdrill.py``): the layer named as the END of a backdrill (``secondary_drill`` /
``backdrill`` for the top side, ``tertiary_drill`` for the bottom side) is itself cut. It maps to
``Backdrill.last_removed_layer``, and ``must_not_cut`` is the next layer inward.
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import numpy as np
import shapely
from oa_model import (
    ArcSeg,
    Backdrill,
    Board,
    BoardMeta,
    BoardOutline,
    Component,
    CopperShape,
    Diagnostic,
    KeepoutRules,
    LineSeg,
    Pad,
    Padstack,
    Points,
    Polygon,
    SourceRef,
    Track,
    Via,
    ViaProtection,
    Zone,
    ZoneFill,
    validate_board,
)
from oa_model.errors import IngestError
from oa_model.padstack import Drill, PadstackLayer, PadShape
from oa_model.setup import ReadOptions
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from . import sexpr
from ._geom import (
    MM,
    PathSegT,
    arc_from_three,
    arc_points,
    clean_ring,
    fix_fractured,
    from_shapely,
    make_arc_seg,
)
from ._tokens import CONTEXTS
from .graphics import (
    FLIP,
    GRAPHIC_HEADS,
    Xf,
    graphic_geometry,
    graphic_segments,
    pts_xy,
)
from .nets import ProjectNets, build_nets, find_project, make_diff_pairs, read_project
from .outline import Piece, bbox_outline, build_outline
from .pads import (
    ShapeParams,
    grow_shape,
    make_shape,
    params_from_node,
    rotate_shape_90,
)
from .stackup import build_stackup, copper_sort_key, read_layer_table

log = logging.getLogger("oa.ingest.kicad")

READER_VERSION = "0.1.0"
MIN_VERSION = 20221018  # KiCad 7.0
MAX_TESTED_VERSION = 20269999  # KiCad 10.x
HEAD_SNIFF = 4096

_PAD_KIND = {
    "smd": "smd",
    "thru_hole": "through_hole",
    "np_thru_hole": "npth",
    "connect": "connector_edge",
}
_ZONE_CONNECT = {"0": "none", "1": "thermal", "2": "solid"}


class KiCadIngestError(IngestError):
    """Fatal read failure with the structured ``path`` / ``hint`` fields (ai-integration.md s6)."""

    code = "INGEST_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        path: str | None = None,
        hint: str | None = None,
        diagnostics: tuple[Diagnostic, ...] = (),
    ) -> None:
        super().__init__(message, code=code)
        self.path = path
        self.hint = hint
        self.diagnostics = diagnostics


class KiCadReader:
    """Native reader for ``.kicad_pcb`` files (entry point ``kicad``)."""

    name: ClassVar[str] = "kicad"
    extensions: ClassVar[tuple[str, ...]] = (".kicad_pcb",)

    @classmethod
    def can_read(cls, path: Path) -> bool:
        """Cheap sniff of the first 4 KB."""
        path = Path(path)
        try:
            with path.open("rb") as f:
                head = f.read(HEAD_SNIFF)
        except OSError:
            return False
        return b"(kicad_pcb" in head

    def read(self, path: Path, options: ReadOptions | None = None) -> Board:
        return _Ingest(Path(path), options if options is not None else ReadOptions()).run()


def _h(*parts: object) -> str:
    return "h:" + hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:16]


class _Ingest:
    def __init__(self, path: Path, options: ReadOptions) -> None:
        self.path = path
        self.options = options
        self.tol = float(getattr(options, "arc_tolerance", 1e-6))
        self.diags: list[Diagnostic] = []
        self.unknown: Counter[tuple[str, str]] = Counter()
        self.ids: set[str] = set()
        self.net_table: dict[int, str] = {}
        self.net_names: set[str] = set()
        self.cu_ids: dict[str, str] = {}  # KiCad copper name -> L<n>
        self.cu_order: list[str] = []
        self.padstacks: dict[tuple[object, ...], Padstack] = {}
        self.pads: list[Pad] = []
        self.vias: list[Via] = []
        self.tracks: list[Track] = []
        self.zones: list[Zone] = []
        self.zone_geoms: dict[tuple[int, str], shapely.Geometry] = {}
        self.shapes: list[CopperShape] = []
        self.components: list[Component] = []
        self.edge_pieces: list[Piece] = []
        self.refdes: set[str] = set()
        self.board_tenting = (False, False)
        self.mask_margin = 0.0
        self.copper_text = 0

    # ------------------------------------------------------------------ helpers
    def diag(
        self,
        severity: str,
        code: str,
        message: str,
        *,
        item: str | None = None,
        location: tuple[float, float] | None = None,
        layer: str | None = None,
        path: str | None = None,
        hint: str | None = None,
    ) -> None:
        self.diags.append(
            Diagnostic(
                severity=severity,  # type: ignore[arg-type]
                code=code,
                message=message,
                item=item,
                location=location,
                layer=layer,
                path=path,
                hint=hint,
            )
        )

    def scan(self, node: sexpr.Node, ctx: str) -> None:
        known = CONTEXTS[ctx]
        for c in node[1:]:
            if type(c) is list and c:
                k = c[0]
                if isinstance(k, str) and k not in known:
                    self.unknown[(ctx, k)] += 1

    def uid(self, node: sexpr.Node, *fallback: object) -> tuple[str, str | None]:
        """Stable unique item id and the raw KiCad uuid."""
        raw = sexpr.get_str(node, "uuid") or sexpr.get_str(node, "tstamp")
        base = f"k:{raw}" if raw else _h(*fallback)
        return self.unique(base), raw

    def unique(self, base: str) -> str:
        if base not in self.ids:
            self.ids.add(base)
            return base
        n = 2
        while f"{base}~{n}" in self.ids:
            n += 1
        out = f"{base}~{n}"
        self.ids.add(out)
        return out

    def net_of(self, node: sexpr.Node) -> str | None:
        n = sexpr.child(node, "net")
        if n is None:
            return None
        vals = [v for v in n[1:] if isinstance(v, str)]
        if not vals:
            return None
        if len(vals) >= 2:
            name: str | None = vals[1]
        elif self.net_table and vals[0].lstrip("-").isdigit():
            name = self.net_table.get(int(vals[0]))
            if name is None and int(vals[0]) != 0:
                self.diag(
                    "warning",
                    "INGEST.NET_UNRESOLVED",
                    f"net number {vals[0]} is not in the net table",
                    hint="re-save the board in KiCad",
                )
        else:
            name = vals[0]
        if not name:
            return None
        self.net_names.add(name)
        return name

    def layer_set(self, names: list[str]) -> list[str]:
        """KiCad copper layer names (top to bottom) selected by names / wildcards."""
        sel: set[str] = set()
        for n in names:
            if n == "*.Cu":
                sel.update(self.cu_order)
            elif n == "F&B.Cu":
                sel.update(x for x in ("F.Cu", "B.Cu") if x in self.cu_ids)
            elif n in self.cu_ids:
                sel.add(n)
        return [n for n in self.cu_order if n in sel]

    def is_cu(self, name: str | None) -> bool:
        return name is not None and name in self.cu_ids

    # ------------------------------------------------------------------ driver
    def run(self) -> Board:
        path = self.path
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise KiCadIngestError(
                f"cannot read {path.name}: {exc}",
                code="INGEST_FILE_UNREADABLE",
                path=str(path),
                hint="check that the file exists and is readable",
            ) from exc
        digest = hashlib.sha256(raw).hexdigest()
        text = raw.decode("utf-8", errors="replace")
        del raw
        try:
            root = sexpr.parse(text)
        except sexpr.SExprError as exc:
            raise KiCadIngestError(
                f"{path.name} is not a valid S-expression file: {exc}",
                code="INGEST_PARSE_ERROR",
                path=str(path),
                hint="open and re-save the board in KiCad, or run 'kicad-cli pcb upgrade'",
            ) from exc
        del text
        if sexpr.head(root) != "kicad_pcb":
            raise KiCadIngestError(
                f"{path.name} is not a .kicad_pcb file (root token {sexpr.head(root)!r})",
                code="INGEST_NOT_KICAD_PCB",
                path=str(path),
                hint="pass a KiCad board file",
            )
        version = int(sexpr.get_float(root, "version", 0) or 0)
        self.check_version(version)
        self.scan(root, "kicad_pcb")

        layers_node = sexpr.child(root, "layers")
        copper = read_layer_table(layers_node)
        if not copper:
            raise KiCadIngestError(
                "board has no copper layers",
                code="INGEST_NO_COPPER",
                path="/layers",
                hint="the (layers ...) table of the file lists no *.Cu layer",
            )
        setup = sexpr.child(root, "setup")
        if setup is not None:
            self.scan(setup, "setup")
            self.board_tenting = self.parse_tenting(setup, (False, False))
            self.mask_margin = (sexpr.get_float(setup, "pad_to_mask_clearance", 0.0) or 0.0) * MM
        general = sexpr.child(root, "general")
        thickness = (sexpr.get_float(general, "thickness", 1.6) or 1.6) * MM if general else 1.6e-3
        sres = build_stackup(setup, copper, thickness)
        self.diags.extend(sres.diagnostics)
        self.cu_ids = sres.layer_ids
        self.cu_order = sres.copper_order
        stackup = sres.stackup

        for n in sexpr.children(root, "net"):
            if len(n) >= 3 and isinstance(n[1], str) and n[1].lstrip("-").isdigit():
                if isinstance(n[2], str) and n[2] != "":
                    self.net_table[int(n[1])] = n[2]
                    self.net_names.add(n[2])
        # the net table is also read when a v9 file lists a net there that no item uses

        self.read_items(root)

        # ---- outline
        outline = build_outline(self.edge_pieces, self.tol, self.diags)
        if outline is None:
            self.diag(
                "warning",
                "INGEST.OUTLINE_MISSING",
                "no closed Edge.Cuts outline found; the copper bounding box is used instead",
                path="/outline",
                hint="draw a closed board outline on Edge.Cuts",
            )
            outline = bbox_outline(self.copper_points())

        # ---- nets, classes, diff pairs
        proj = ProjectNets()
        popt = getattr(self.options, "project_file", None)
        if popt is not None:
            pro, exact = Path(popt), True
        else:
            pro, exact = find_project(path)
        if pro is not None and Path(pro).is_file():
            if not exact:
                self.diag(
                    "info",
                    "INGEST.PROJECT_GUESSED",
                    f"using {Path(pro).name} as the project file (no file with the board's name)",
                )
            proj = read_project(Path(pro), self.diags)
        elif popt is not None:
            self.diag(
                "warning",
                "INGEST.PROJECT_MISSING",
                f"project file {popt} does not exist; net classes are skipped",
                hint="pass an existing .kicad_pro as ReadOptions.project_file",
            )
        else:
            self.diag(
                "info",
                "INGEST.PROJECT_MISSING",
                "no .kicad_pro found next to the board; net classes are skipped",
            )
        nets, cls_of = build_nets(self.net_names, proj)
        classes = {c.name: c for c in proj.classes}
        diff_pairs = make_diff_pairs(self.net_names, classes, cls_of)

        # ---- zones: stale detection
        self.detect_stale_zones()

        for (ctx, key), n in sorted(self.unknown.items()):
            self.diag(
                "info",
                "INGEST.UNKNOWN_TOKEN",
                f"unknown token '{key}' inside '{ctx}' ({n} occurrence{'s' if n != 1 else ''}); ignored",
                hint="the reader tolerates unknown tokens; report it if geometry looks wrong",
            )
        if self.copper_text:
            self.diag(
                "info",
                "INGEST.COPPER_TEXT_SKIPPED",
                f"{self.copper_text} text item(s) on copper layers are not converted to copper",
            )

        meta = BoardMeta(
            name=path.stem,
            source_tool="kicad",
            source_version=str(version) if version else None,
            source_file=path.name,
            source_hash=digest,
            reader=f"oa-ingest-kicad {READER_VERSION}",
            arc_tolerance=self.tol,
            created_utc=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        board = Board(
            meta=meta,
            outline=outline,
            stackup=stackup,
            nets=nets,
            net_classes=proj.classes,
            diff_pairs=diff_pairs,
            padstacks=tuple(self.padstacks.values()),
            pads=tuple(self.pads),
            vias=tuple(self.vias),
            tracks=tuple(self.tracks),
            zones=tuple(self.zones),
            shapes=tuple(self.shapes),
            components=tuple(self.components),
            diagnostics=tuple(self.diags),
        )
        extra = tuple(d for d in validate_board(board) if d.severity != "info")
        if extra:
            board = board.model_copy(update={"diagnostics": board.diagnostics + extra})
        if getattr(self.options, "strict", False):
            bad = [d for d in board.diagnostics if d.severity != "info"]
            if bad:
                raise KiCadIngestError(
                    f"{len(bad)} warning/error diagnostic(s) in strict mode; first: "
                    f"{bad[0].code} {bad[0].message}",
                    code="INGEST_STRICT",
                    path=bad[0].path,
                    hint=bad[0].hint,
                    diagnostics=tuple(bad),
                )
        return board

    def check_version(self, version: int) -> None:
        if version == 0:
            self.diag("warning", "INGEST.VERSION_UNKNOWN", "file has no (version ...) token")
        elif version < MIN_VERSION:
            self.diag(
                "warning",
                "INGEST.VERSION_OLD",
                f"format version {version} is older than the supported minimum {MIN_VERSION} "
                "(KiCad 7); reading best effort",
                hint="open and save the board in a current KiCad, or run 'kicad-cli pcb upgrade'",
            )
        elif version > MAX_TESTED_VERSION:
            self.diag(
                "warning",
                "INGEST.VERSION_NEWER",
                f"format version {version} is newer than the last tested (KiCad 10)",
                hint="update oa-ingest-kicad; unknown tokens are ignored",
            )

    def copper_points(self) -> np.ndarray:
        parts = []
        if self.tracks:
            parts.append(
                np.array(
                    [(s.start[0], s.start[1]) for t in self.tracks for s in t.path[:1]]
                    + [(s.end[0], s.end[1]) for t in self.tracks for s in t.path[-1:]]
                )
            )
        if self.pads:
            parts.append(np.array([p.position for p in self.pads]))
        if self.vias:
            parts.append(np.array([v.position for v in self.vias]))
        return np.vstack(parts) if parts else np.zeros((0, 2))

    # ------------------------------------------------------------------ items
    def read_items(self, root: sexpr.Node) -> None:
        for n in root[1:]:
            if type(n) is not list or not n:
                continue
            h = n[0]
            if h == "segment":
                self.segment(n)
            elif h == "arc":
                self.track_arc(n)
            elif h == "via":
                self.via(n)
            elif h == "footprint" or h == "module":
                self.footprint(n)
            elif h == "zone":
                self.zone(n, Xf())
            elif h in GRAPHIC_HEADS:
                self.board_graphic(n)
            elif h in ("gr_text", "gr_text_box"):
                if self.is_cu(sexpr.get_str(n, "layer")):
                    self.copper_text += 1

    # ------------------------------------------------------------------ tracks
    def segment(self, n: sexpr.Node) -> None:
        self.scan(n, "segment")
        layer = sexpr.get_str(n, "layer")
        if layer not in self.cu_ids:
            return
        s, e = sexpr.get_floats(n, "start"), sexpr.get_floats(n, "end")
        w = sexpr.get_float(n, "width")
        if s is None or e is None or len(s) < 2 or len(e) < 2 or not w or w <= 0:
            self.diag("warning", "INGEST.TRACK_INVALID", "segment without usable start/end/width")
            return
        a = (s[0] * MM + 0.0, 0.0 - s[1] * MM)
        b = (e[0] * MM + 0.0, 0.0 - e[1] * MM)
        net = self.net_of(n)
        tid, raw = self.uid(n, "segment", layer, net, a, b, w)
        self.tracks.append(
            Track(
                id=tid,
                net=net,
                layer=self.cu_ids[layer],
                width=w * MM,
                path=(LineSeg(start=a, end=b),),
                source=SourceRef(tool="kicad", uuid=raw, token="segment"),
            )
        )

    def track_arc(self, n: sexpr.Node) -> None:
        self.scan(n, "arc")
        layer = sexpr.get_str(n, "layer")
        if layer not in self.cu_ids:
            return
        s, m, e = (sexpr.get_floats(n, k) for k in ("start", "mid", "end"))
        w = sexpr.get_float(n, "width")
        if (
            s is None or m is None or e is None or len(s) < 2 or len(m) < 2 or len(e) < 2
            or not w or w <= 0
        ):  # fmt: skip
            self.diag("warning", "INGEST.TRACK_INVALID", "arc without usable start/mid/end/width")
            return
        pa = FLIP.pt(s[0], s[1])
        pm = FLIP.pt(m[0], m[1])
        pb = FLIP.pt(e[0], e[1])
        seg = make_arc_seg(pa, pm, pb)
        if isinstance(seg, LineSeg) and arc_from_three(pa, pm, pb) is not None:
            self.diag(
                "warning",
                "INGEST.ARC_DEGENERATE",
                "track arc is numerically inconsistent; replaced by a straight segment",
                location=pa,
            )
        net = self.net_of(n)
        tid, raw = self.uid(n, "arc", layer, net, pa, pm, pb, w)
        self.tracks.append(
            Track(
                id=tid,
                net=net,
                layer=self.cu_ids[layer],
                width=w * MM,
                path=(seg,),
                source=SourceRef(tool="kicad", uuid=raw, token="arc"),
            )
        )

    # ------------------------------------------------------------------ vias
    def fb_flags(
        self, node: sexpr.Node, key: str, default: tuple[bool, bool]
    ) -> tuple[bool, bool]:
        """(front, back) flags of ``(key front back)`` or ``(key (front yes) (back no))``."""
        t = sexpr.child(node, key)
        if t is None:
            return default
        front = back = False
        explicit = False
        for c in t[1:]:
            if isinstance(c, str):
                if c in ("front", "back"):
                    explicit = True
                    front = front or c == "front"
                    back = back or c == "back"
            elif isinstance(c, list) and c and c[0] in ("front", "back"):
                explicit = True
                val = len(c) < 2 or str(c[1]).lower() not in ("no", "false", "0")
                if c[0] == "front":
                    front = val
                else:
                    back = val
        return (front, back) if explicit else default

    def parse_tenting(self, node: sexpr.Node, default: tuple[bool, bool]) -> tuple[bool, bool]:
        return self.fb_flags(node, "tenting", default)

    def via(self, n: sexpr.Node) -> None:
        self.scan(n, "via")
        at = sexpr.get_floats(n, "at")
        lay = sexpr.child(n, "layers")
        size = sexpr.get_float(n, "size")
        drill = sexpr.get_float(n, "drill")
        if at is None or len(at) < 2 or lay is None or not size:
            self.diag("warning", "INGEST.VIA_INVALID", "via without position, size or layers")
            return
        names = [v for v in lay[1:] if isinstance(v, str)]
        if len(names) < 2 or names[0] not in self.cu_ids or names[1] not in self.cu_ids:
            self.diag(
                "warning",
                "INGEST.VIA_INVALID",
                f"via on unknown layers {names}",
                location=FLIP.pt(at[0], at[1]),
            )
            return
        pos = FLIP.pt(at[0], at[1])
        i0, i1 = sorted((self.cu_order.index(names[0]), self.cu_order.index(names[1])))
        span = self.cu_order[i0 : i1 + 1]
        ncu = len(self.cu_order)
        declared = next((a for a in n[1:] if a in ("blind", "buried", "micro")), None)
        if i0 == 0 and i1 == ncu - 1:
            kind = "through"
        elif declared == "micro" and i1 - i0 == 1:
            kind = "micro"
        elif i0 == 0 or i1 == ncu - 1:
            kind = "blind"
        else:
            kind = "buried"
        net = self.net_of(n)
        base_params = ShapeParams(shape="circle", w=size, h=size)
        ps_node = sexpr.child(n, "padstack")
        layers = self.padstack_layers(ps_node, span, base_params, None, None, None)
        remove_unused = sexpr.get_bool(n, "remove_unused_layers")
        keep_end = sexpr.get_bool(n, "keep_end_layers")
        dr = Drill(kind="plated", diameter=(drill or 0.0) * MM) if drill else None
        ps = self.intern_padstack(dr, layers, None, None, remove_unused, keep_end)
        bds = self.backdrills(n, span, i0, pos)
        tt = self.parse_tenting(n, self.board_tenting)
        cov = self.fb_flags(n, "plugging", (False, False))
        prot = ViaProtection(
            tented_top=tt[0],
            tented_bottom=tt[1],
            plugged=cov[0] or cov[1],
            filled="unknown" if sexpr.get_bool(n, "filling") else "none",
            capped=sexpr.get_bool(n, "capping"),
        )
        vid, raw = self.uid(n, "via", names, pos, size)
        self.vias.append(
            Via(
                id=vid,
                padstack=ps.id,
                position=pos,
                net=net,
                kind=kind,  # type: ignore[arg-type]
                start_layer=self.cu_ids[span[0]],
                end_layer=self.cu_ids[span[-1]],
                backdrills=bds,
                protection=prot,
                source=SourceRef(tool="kicad", uuid=raw, token="via"),
            )
        )

    def backdrills(
        self, n: sexpr.Node, span: list[str], i0: int, pos: tuple[float, float]
    ) -> tuple[Backdrill, ...]:
        out: list[Backdrill] = []
        for token, default_side in (
            ("backdrill", "top"),
            ("secondary_drill", "top"),
            ("tertiary_drill", "bottom"),
        ):
            bd = sexpr.child(n, token)
            if bd is None:
                continue
            size = sexpr.get_float(bd, "size")
            lay = sexpr.child(bd, "layers")
            names = [v for v in lay[1:] if isinstance(v, str)] if lay else []
            if not size or len(names) < 2 or names[1] not in span or names[0] not in self.cu_ids:
                self.diag(
                    "warning",
                    "INGEST.BACKDRILL_INVALID",
                    f"backdrill '{token}' on unusable layers {names}; ignored",
                    location=pos,
                )
                continue
            side = default_side
            if names[0] == self.cu_order[0]:
                side = "top"
            elif names[0] == self.cu_order[-1]:
                side = "bottom"
            k = span.index(names[1])
            inward = k + 1 if side == "top" else k - 1
            if inward < 0 or inward >= len(span):
                self.diag(
                    "warning",
                    "INGEST.BACKDRILL_INVALID",
                    "backdrill removes the whole barrel; ignored",
                    location=pos,
                )
                continue
            out.append(
                Backdrill(
                    side=side,  # type: ignore[arg-type]
                    diameter=size * MM,
                    last_removed_layer=self.cu_ids[names[1]],
                    must_not_cut=self.cu_ids[span[inward]],
                )
            )
        return tuple(out)

    # ------------------------------------------------------------------ padstacks
    def padstack_layers(
        self,
        ps_node: sexpr.Node | None,
        layer_names: list[str],
        base: ShapeParams,
        thermal_gap: float | None,
        spoke: float | None,
        connect: str | None,
    ) -> tuple[PadstackLayer, ...]:
        """Resolve a pad/via padstack into one entry per conductor layer."""
        mode = (sexpr.get_str(ps_node, "mode") if ps_node is not None else None) or "normal"
        per: dict[str, sexpr.Node] = {}
        if ps_node is not None:
            for ln in sexpr.children(ps_node, "layer"):
                if len(ln) > 1 and isinstance(ln[1], str):
                    per[ln[1]] = ln
        cache: dict[str, PadShape | None] = {}
        out: list[PadstackLayer] = []
        for name in layer_names:
            if mode == "front_inner_back":
                key = name if name in (self.cu_order[0], self.cu_order[-1]) else "Inner"
            elif mode == "custom":
                key = name
            else:
                key = ""
            ln = per.get(key) if key else None
            if ln is not None:
                p = base.merged(params_from_node(ln))
                tg = sexpr.get_float(ln, "thermal_gap", thermal_gap)
                sw = sexpr.get_float(ln, "thermal_bridge_width", spoke)
                zc = sexpr.get_str(ln, "zone_connect", connect)
            else:
                p, tg, sw, zc = base, thermal_gap, spoke, connect
            ckey = f"{id(ln)}"
            if ckey not in cache:
                cache[ckey] = make_shape(p, self.tol)
            out.append(
                PadstackLayer(
                    layer=self.cu_ids[name],
                    pad=cache[ckey],
                    thermal_spoke_width=sw * MM if sw else None,
                    thermal_gap=tg * MM if tg else None,
                    connection=_ZONE_CONNECT.get(zc or "", "unknown"),  # type: ignore[arg-type]
                )
            )
        return tuple(out)

    def intern_padstack(
        self,
        drill: Drill | None,
        layers: tuple[PadstackLayer, ...],
        mask_top: PadShape | None,
        mask_bottom: PadShape | None,
        remove_unused: bool,
        keep_end: bool,
    ) -> Padstack:
        key = (drill, layers, mask_top, mask_bottom, remove_unused, keep_end)
        ps = self.padstacks.get(key)
        if ps is None:
            ps = Padstack(
                id=f"ps:{len(self.padstacks) + 1}",
                drill=drill,
                layers=layers,
                remove_unused_layers=remove_unused,
                keep_end_layers=keep_end,
                mask_opening_top=mask_top,
                mask_opening_bottom=mask_bottom,
            )
            self.padstacks[key] = ps
        return ps

    # ------------------------------------------------------------------ footprints
    def footprint(self, fp: sexpr.Node) -> None:
        self.scan(fp, "footprint")
        lib = fp[1] if len(fp) > 1 and isinstance(fp[1], str) else None
        at = sexpr.get_floats(fp, "at") or [0.0, 0.0]
        x, y = at[0], at[1] if len(at) > 1 else 0.0
        rot = at[2] if len(at) > 2 else 0.0
        side = "bottom" if sexpr.get_str(fp, "layer") == "B.Cu" else "top"
        xf = Xf.footprint(x, y, rot)
        pos = (x * MM + 0.0, 0.0 - y * MM)

        props: list[tuple[str, str]] = []
        ref = value = None
        for c in fp[2:]:
            if type(c) is not list or not c:
                continue
            if c[0] == "property" and len(c) >= 3 and isinstance(c[1], str):
                props.append((c[1], str(c[2])))
            elif c[0] == "fp_text" and len(c) >= 3 and c[1] in ("reference", "value"):
                if c[1] == "reference":
                    ref = str(c[2])
                else:
                    value = str(c[2])
        for k, v in props:
            if k == "Reference":
                ref = v
            elif k == "Value":
                value = v
        if not ref:
            ref = "REF**"
        refdes = ref
        k = 2
        while refdes in self.refdes:
            refdes = f"{ref}#{k}"
            k += 1
        if refdes != ref:
            self.diag(
                "warning",
                "INGEST.DUPLICATE_REFDES",
                f"reference designator {ref!r} is used more than once; renamed {refdes!r}",
                item=refdes,
                location=pos,
                hint="annotate the board so every footprint has a unique reference",
            )
        self.refdes.add(refdes)

        attr = sexpr.child(fp, "attr")
        pad_ids: list[str] = []
        courtyard: list[object] = []
        for c in fp[2:]:
            if type(c) is not list or not c:
                continue
            h = c[0]
            if h == "pad":
                pid = self.pad(c, refdes, xf, side)
                if pid:
                    pad_ids.append(pid)
            elif h in GRAPHIC_HEADS:
                layer = sexpr.get_str(c, "layer")
                if layer in self.cu_ids:
                    self.copper_graphic(c, xf, layer, None, "fp")
                elif layer == "Edge.Cuts":
                    self.edge_graphic(c, xf)
                elif layer in ("F.CrtYd", "B.CrtYd"):
                    g = graphic_geometry(c, xf, self.tol)
                    if g is not None and not g.is_empty:
                        courtyard.append(g)
            elif h == "zone":
                self.zone(c, Xf())
            elif h in ("fp_text", "fp_text_box") and self.is_cu(sexpr.get_str(c, "layer")):
                self.copper_text += 1
        fields = tuple((k2, v) for k2, v in props if k2 not in _SKIP_FIELDS and v)
        model = sexpr.get_str(fp, "model")
        cy = None
        if courtyard:
            polys = from_shapely(shapely.union_all(courtyard).buffer(0))
            if polys:
                cy = max(polys, key=lambda p: abs(_area(p)))
        self.components.append(
            Component(
                refdes=refdes,
                value=value or None,
                footprint=lib,
                side=side,  # type: ignore[arg-type]
                position=pos,
                rotation=math.radians(rot),
                pads=tuple(pad_ids),
                courtyard=cy,
                dnp=bool(attr and sexpr.has_atom(attr, "dnp")),
                exclude_from_bom=bool(attr and sexpr.has_atom(attr, "exclude_from_bom")),
                fields=fields,
                model_ref=model,
                source=SourceRef(tool="kicad", uuid=sexpr.get_str(fp, "uuid"), token="footprint"),
            )
        )

    def pad(self, p: sexpr.Node, refdes: str, xf: Xf, side: str) -> str | None:
        self.scan(p, "pad")
        atoms = [a for a in p[1:4] if isinstance(a, str)]
        if len(atoms) < 3:
            self.diag("warning", "INGEST.PAD_INVALID", "pad without number, type and shape", item=refdes)
            return None
        number, ptype, shape = atoms[0], atoms[1], atoms[2]
        kind = _PAD_KIND.get(ptype)
        if kind is None:
            self.diag("warning", "INGEST.PAD_INVALID", f"unknown pad type {ptype!r}", item=refdes)
            return None
        at = sexpr.get_floats(p, "at") or [0.0, 0.0]
        pos = xf.pt(at[0], at[1] if len(at) > 1 else 0.0)
        rot = math.radians(at[2]) if len(at) > 2 else 0.0  # ABSOLUTE board-frame angle
        lay = sexpr.child(p, "layers")
        cu = self.layer_set([v for v in lay[1:] if isinstance(v, str)] if lay else [])
        names_all = [v for v in lay[1:] if isinstance(v, str)] if lay else []
        base = params_from_node(p, shape)
        tg = sexpr.get_float(p, "thermal_gap")
        sw = sexpr.get_float(p, "thermal_bridge_width", sexpr.get_float(p, "thermal_width"))
        zc = sexpr.get_str(p, "zone_connect")
        layers = self.padstack_layers(sexpr.child(p, "padstack"), cu, base, tg, sw, zc)

        drill = None
        dn = sexpr.child(p, "drill")
        if dn is not None and kind in ("through_hole", "npth"):
            drill = self.pad_drill(dn, p, kind)
        swap = False
        if dn is not None and drill is not None and drill.shape == "slot":
            dv = [float(v) for v in dn[1:] if isinstance(v, str) and _isnum(v)]
            if len(dv) >= 2 and dv[1] > dv[0]:
                swap = True
        if swap and drill is not None:
            # the slot runs along local y: rotate the pad frame by +90 so it runs along local x
            layers = tuple(
                lyr.model_copy(
                    update={
                        "pad": rotate_shape_90(lyr.pad),
                        "antipad": rotate_shape_90(lyr.antipad),
                    }
                )
                for lyr in layers
            )
            drill = drill.model_copy(update={"offset": (drill.offset[1], -drill.offset[0])})
            rot += math.pi / 2.0
        mt = mb = None
        if names_all:
            margin = (
                sexpr.get_float(p, "solder_mask_margin", 0.0) or 0.0
            ) * MM + self.mask_margin
            first = layers[0].pad if layers else None
            last = layers[-1].pad if layers else None
            if "F.Mask" in names_all or "*.Mask" in names_all:
                mt = grow_shape(first if cu and cu[0] == self.cu_order[0] else None, margin)
            if "B.Mask" in names_all or "*.Mask" in names_all:
                mb = grow_shape(last if cu and cu[-1] == self.cu_order[-1] else None, margin)
        ps = self.intern_padstack(
            drill,
            layers,
            mt,
            mb,
            sexpr.get_bool(p, "remove_unused_layers"),
            sexpr.get_bool(p, "keep_end_layers"),
        )
        net = self.net_of(p)
        pid, raw = self.uid(p, "pad", refdes, number, pos)
        dl = sexpr.get_float(p, "die_length")
        dd = sexpr.get_float(p, "die_delay")
        self.pads.append(
            Pad(
                id=pid,
                component=refdes,
                number=number,
                padstack=ps.id,
                position=pos,
                rotation=rot,
                net=net,
                kind=kind,  # type: ignore[arg-type]
                pin_function=sexpr.get_str(p, "pinfunction"),
                pin_type=sexpr.get_str(p, "pintype"),
                die_length=dl * MM if dl else None,
                die_delay=dd * 1e-12 if dd else None,  # KiCad stores picoseconds
                source=SourceRef(tool="kicad", uuid=raw, token="pad"),
            )
        )
        return pid

    def pad_drill(self, dn: sexpr.Node, pad: sexpr.Node, kind: str) -> Drill | None:
        vals = [float(v) for v in dn[1:] if isinstance(v, str) and _isnum(v)]
        oval = any(v == "oval" for v in dn[1:] if isinstance(v, str))
        off = sexpr.get_floats(dn, "offset") or sexpr.get_floats(pad, "offset") or [0.0, 0.0]
        if not vals or vals[0] <= 0:
            return None
        offset = (off[0] * MM + 0.0, 0.0 - (off[1] if len(off) > 1 else 0.0) * MM)
        dk = "plated" if kind == "through_hole" else "npth"
        if oval and len(vals) >= 2 and abs(vals[0] - vals[1]) > 1e-9:
            return Drill(
                kind=dk,  # type: ignore[arg-type]
                shape="slot",
                diameter=min(vals[0], vals[1]) * MM,
                slot_length=max(vals[0], vals[1]) * MM,
                offset=offset,
            )
        return Drill(kind=dk, diameter=vals[0] * MM, offset=offset)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ graphics
    def board_graphic(self, n: sexpr.Node) -> None:
        layer = sexpr.get_str(n, "layer")
        if layer == "Edge.Cuts":
            self.edge_graphic(n, FLIP)
        elif layer in self.cu_ids:
            self.copper_graphic(n, FLIP, layer, self.net_of(n), "gr")

    def edge_graphic(self, n: sexpr.Node, xf: Xf) -> None:
        r = graphic_segments(n, xf, self.tol)
        if r is None:
            self.diag("warning", "INGEST.OUTLINE_ITEM_INVALID", f"unusable {n[0]} on Edge.Cuts")
            return
        closed, segs = r
        self.edge_pieces.append(Piece(closed, segs))

    def copper_graphic(
        self, n: sexpr.Node, xf: Xf, layer: str, net: str | None, prefix: str
    ) -> None:
        g = graphic_geometry(n, xf, self.tol)
        if g is None or g.is_empty:
            return
        polys = from_shapely(g)
        raw = sexpr.get_str(n, "uuid") or sexpr.get_str(n, "tstamp")
        for i, poly in enumerate(polys):
            base = f"k:{raw}" if raw else _h(prefix, n[0], layer, net, poly.exterior.array[0])
            sid = self.unique(base if i == 0 else f"{base}/{i}")
            self.shapes.append(
                CopperShape(
                    id=sid,
                    net=net,
                    layer=self.cu_ids[layer],
                    polygon=poly,
                    origin="graphic",
                    source=SourceRef(tool="kicad", uuid=raw, token=str(n[0])),
                )
            )

    # ------------------------------------------------------------------ zones
    def zone(self, z: sexpr.Node, xf: Xf) -> None:
        self.scan(z, "zone")
        net = None
        nn = sexpr.get_str(z, "net_name")
        if nn:
            net = nn
            self.net_names.add(nn)
        else:
            net = self.net_of(z)
        lay = sexpr.child(z, "layers")
        ln = sexpr.child(z, "layer")
        names = (
            [v for v in lay[1:] if isinstance(v, str)]
            if lay
            else [v for v in ln[1:] if isinstance(v, str)] if ln else []
        )
        cu = self.layer_set(names)
        if not cu:
            return  # zone on a non-copper layer (graphic zones are not copper)
        zid, raw = self.uid(z, "zone", names, net)
        keep = sexpr.child(z, "keepout")
        kr = None
        if keep is not None:
            def forbid(key: str) -> bool:
                v = sexpr.get_str(keep, key)
                return v is not None and v.lower() == "not_allowed"

            kr = KeepoutRules(
                tracks=forbid("tracks"),
                vias=forbid("vias"),
                pads=forbid("pads"),
                copper_pour=forbid("copperpour"),
                footprints=forbid("footprints"),
            )
            net = None
        outline = self.zone_outline(z, xf, zid)
        if outline is None:
            self.diag(
                "warning", "INGEST.ZONE_INVALID", "zone without a usable outline", item=zid
            )
            return
        zi = len(self.zones)
        # ---- fills
        by_layer: dict[str, list[object]] = {}
        for fpn in sexpr.children(z, "filled_polygon"):
            fl = sexpr.get_str(fpn, "layer")
            pts = sexpr.child(fpn, "pts")
            if fl not in self.cu_ids or pts is None:
                continue
            ring = clean_ring(pts_xy(pts, FLIP, self.tol))
            if ring.shape[0] < 3:
                continue
            by_layer.setdefault(fl, []).append(fix_fractured(ring))
        fills: list[ZoneFill] = []
        for kname in cu:
            geoms = by_layer.get(kname)
            if not geoms:
                continue
            g = geoms[0] if len(geoms) == 1 else shapely.union_all(geoms)
            polys = from_shapely(g)
            if polys:
                fills.append(ZoneFill(layer=self.cu_ids[kname], polygons=polys))
                self.zone_geoms[(zi, kname)] = g
        fill_node = sexpr.child(z, "fill")
        wants_fill = kr is None and fill_node is not None and sexpr.has_atom(fill_node, "yes")
        hatch = False
        if fill_node is not None:
            md = sexpr.get_str(fill_node, "mode")
            hatch = md == "hatch"
        state = "filled"
        if kr is None and not fills:
            state = "unfilled"
            self.diag(
                "warning",
                "INGEST.ZONE_UNFILLED",
                f"zone {zid!r} (net {net!r}) has no filled copper"
                + ("" if wants_fill else " (fill flag is off)"),
                item=zid,
                location=(float(outline.exterior.array[0][0]), float(outline.exterior.array[0][1])),
                path=f"/zones/{zi}",
                hint="refill the zones in KiCad (press B) and save, or read with refill_zones",
            )
        mt = sexpr.get_float(z, "min_thickness")
        self.zones.append(
            Zone(
                id=zid,
                net=net,
                layers=tuple(self.cu_ids[k] for k in cu),
                priority=int(sexpr.get_float(z, "priority", 0) or 0),
                outline=outline,
                fills=tuple(fills),
                fill_state=state,  # type: ignore[arg-type]
                hatch=hatch,
                is_teardrop=sexpr.child(z, "teardrop") is not None,
                keepout=kr,
                min_thickness=mt * MM if mt else None,
                source=SourceRef(tool="kicad", uuid=raw, token="zone"),
            )
        )

    def zone_outline(self, z: sexpr.Node, xf: Xf, zid: str) -> Polygon | None:
        pn = sexpr.child(z, "polygon")
        pts = sexpr.child(pn, "pts") if pn is not None else None
        ring = clean_ring(pts_xy(pts, xf, self.tol)) if pts is not None else np.zeros((0, 2))
        if ring.shape[0] < 3:
            return None
        g = fix_fractured(ring)
        polys = from_shapely(g)
        if not polys:
            return None
        if len(polys) > 1:
            self.diag(
                "warning",
                "INGEST.ZONE_OUTLINE_SELF_INTERSECTS",
                f"zone {zid!r} outline self-intersects; the largest part is kept",
                item=zid,
                hint="fix the zone outline in KiCad",
            )
        return max(polys, key=lambda p: abs(_area(p)))

    def detect_stale_zones(self) -> None:
        """Flag fills that overlap foreign-net copper (tracks, vias): a refill was skipped."""
        if not self.zone_geoms:
            return
        id_to_name = {v: k for k, v in self.cu_ids.items()}
        by_layer_tracks: dict[str, tuple[STRtree, list[Track]]] = {}
        for lid in self.cu_ids.values():
            ts = [t for t in self.tracks if t.layer == lid]
            if ts:
                geoms = [_track_line(t) for t in ts]
                by_layer_tracks[lid] = (STRtree(geoms), ts)
        vias_by_layer: dict[str, list[Via]] = {}
        order = list(self.cu_ids.values())
        for v in self.vias:
            s, e = order.index(v.start_layer), order.index(v.end_layer)
            for lid in order[s : e + 1]:
                vias_by_layer.setdefault(lid, []).append(v)
        via_trees: dict[str, STRtree] = {}
        via_size: dict[str, float] = {}
        for ps in self.padstacks.values():
            sizes = [
                getattr(lyr.pad, "diameter", None) for lyr in ps.layers if lyr.pad is not None
            ]
            via_size[ps.id] = max((s for s in sizes if s), default=0.0)
        for lid, vs in vias_by_layer.items():
            via_trees[lid] = STRtree([Point(v.position) for v in vs])

        stale: set[int] = set()
        for (zi, kname), geom in self.zone_geoms.items():
            z = self.zones[zi]
            if z.net is None or zi in stale:
                continue
            lid = self.cu_ids[kname]
            hit = None
            if lid in by_layer_tracks:
                tree, ts = by_layer_tracks[lid]
                for j in tree.query(geom, predicate="intersects"):
                    t = ts[int(j)]
                    if t.net is None or t.net == z.net:
                        continue
                    area = _track_line(t).buffer(t.width / 2).intersection(geom).area
                    total = max(t.width * _track_len(t), 1e-18)
                    if area / total > 0.05:
                        hit = (t.id, t.path[0].start)
                        break
            if hit is None and lid in via_trees:
                vs = vias_by_layer[lid]
                for j in via_trees[lid].query(geom, predicate="intersects"):
                    v = vs[int(j)]
                    if v.net is None or v.net == z.net:
                        continue
                    r = via_size.get(v.padstack, 0.0) / 2
                    if r <= 0:
                        continue
                    disc = Point(v.position).buffer(r, quad_segs=8)
                    if disc.intersection(geom).area / disc.area > 0.05:
                        hit = (v.id, v.position)
                        break
            if hit is not None:
                stale.add(zi)
                self.zones[zi] = z.model_copy(update={"fill_state": "stale_suspected"})
                self.diag(
                    "warning",
                    "INGEST.ZONE_STALE",
                    f"zone {z.id!r} (net {z.net!r}) fill on {id_to_name.get(lid, lid)} overlaps "
                    f"copper of another net ({hit[0]}); the fill is probably out of date",
                    item=z.id,
                    location=(float(hit[1][0]), float(hit[1][1])),
                    layer=lid,
                    path=f"/zones/{zi}",
                    hint="refill all zones in KiCad (Edit > Fill All Zones) and save",
                )


_SKIP_FIELDS = frozenset({"Reference", "Value", "Footprint", "Datasheet", "Description"})


def _isnum(s: str) -> bool:
    try:
        float(s)
    except ValueError:
        return False
    return True


def _area(p: Polygon) -> float:
    a = p.exterior.array
    return float(0.5 * np.sum(a[:, 0] * np.roll(a[:, 1], -1) - np.roll(a[:, 0], -1) * a[:, 1]))


def _track_line(t: Track) -> LineString:
    pts: list[tuple[float, float]] = [t.path[0].start]
    for s in t.path:
        if isinstance(s, ArcSeg):
            from ._geom import Arc

            ap = arc_points(Arc(s.start, s.center, s.end, s.ccw), 1e-4)
            pts.extend(map(tuple, ap[1:].tolist()))
        else:
            pts.append(s.end)
    return LineString(pts)


def _track_len(t: Track) -> float:
    from ._geom import path_length

    return path_length(t.path)


__all__ = ["KiCadIngestError", "KiCadReader", "PathSegT"]
