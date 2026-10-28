# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``BoardSession``: board views (summary, nets, net, stackup) over an in-memory ``Board``.

Used by both the real and the fake service, so agents see the same shapes either way. All lists
are totally ordered (nets by name, layers top first) and capped per ai-integration.md section 8.3.
"""

from __future__ import annotations

import fnmatch
import math
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from oa_model.board import Board
from oa_model.geometry import ArcSeg, LineSeg
from oa_model.setup import AnalysisSetup, ResolvedIntent
from oa_model.stackup import ConductorLayer, DielectricLayer, MaskLayer
from oa_physics.dielectric import eps_complex
from oa_service.errors import ServiceError, close_matches
from oa_service.models import (
    BoardMetaView,
    BoardSummary,
    CopperLayerInfo,
    CountBy,
    DiagnosticDigest,
    IntentBrief,
    LayerRun,
    NetClassInfo,
    NetDetail,
    NetInfo,
    NetQuery,
    PadBrief,
    ProjectHandle,
    SetupPutOut,
    SetupSummary,
    StackLayerView,
    StackupView,
    ViaBrief,
    ZoneCounts,
)
from oa_service.types import Page, paginate

__all__ = ["BoardSession", "project_id_for"]

DEFAULT_FREQUENCY_HZ = 1e9
_MAX_VIAS = 50
_MAX_PADS = 100


def project_id_for(path: Path) -> str:
    """Stable project id: ``'p_' + sha1(absolute path)[:12]``."""
    import hashlib

    return "p_" + hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:12]


def _seg_length(seg: LineSeg | ArcSeg) -> float:
    if isinstance(seg, LineSeg):
        return math.hypot(seg.end[0] - seg.start[0], seg.end[1] - seg.start[1])
    r = math.hypot(seg.start[0] - seg.center[0], seg.start[1] - seg.center[1])
    a0 = math.atan2(seg.start[1] - seg.center[1], seg.start[0] - seg.center[0])
    a1 = math.atan2(seg.end[1] - seg.center[1], seg.end[0] - seg.center[0])
    d = (a1 - a0) % (2 * math.pi) if seg.ccw else (a0 - a1) % (2 * math.pi)
    return r * d


def _diag_digest(board: Board) -> DiagnosticDigest:
    order = {"error": 0, "warning": 1, "info": 2}
    counts = Counter(d.severity for d in board.diagnostics)
    top = sorted(board.diagnostics, key=lambda d: (order[d.severity], d.code, d.message))[:20]
    return DiagnosticDigest(counts=dict(sorted(counts.items())), top=tuple(top))


class BoardSession:
    """A board plus its resolved intent and stored setups, addressed by a stable id."""

    def __init__(
        self,
        *,
        project_id: str,
        name: str,
        board: Board,
        raw_board_hash: str,
        intent: ResolvedIntent,
        board_path: str,
        project_path: str | None,
        ephemeral: bool,
        physics_hash: str | None = None,
        setups_dir: Path | None = None,
        geometry_factory: Callable[[Board], Any] | None = None,
        watch_paths: tuple[Path, ...] = (),
    ) -> None:
        self.id = project_id
        self.name = name
        self.board = board
        self.raw_board_hash = raw_board_hash
        self.intent = intent
        self.board_path = board_path
        self.project_path = project_path
        self.ephemeral = ephemeral
        self.physics_hash = physics_hash
        self.setups_dir = setups_dir
        self.watch_paths = watch_paths
        self._geometry_factory = geometry_factory
        self._geometry: Any = None
        self._memory_setups: dict[str, AnalysisSetup] = {}
        self._by_net = intent.index()

    # -- handle / summary ------------------------------------------------------------------

    def handle(self) -> ProjectHandle:
        return ProjectHandle(
            project_id=self.id,
            name=self.name,
            project_path=self.project_path,
            board_path=self.board_path,
            ephemeral=self.ephemeral,
            board_hash=self.raw_board_hash,
            physics_hash=self.physics_hash,
            source_tool=self.board.meta.source_tool,
            diagnostics=_diag_digest(self.board),
            summary=self.summary(),
        )

    def summary(self) -> BoardSummary:
        b = self.board
        xs = list(b.outline.polygon.exterior.array[:, 0])
        ys = list(b.outline.polygon.exterior.array[:, 1])
        size = (float(max(xs) - min(xs)), float(max(ys) - min(ys))) if xs and ys else (0.0, 0.0)
        copper = [layer for layer in b.stackup.layers if isinstance(layer, ConductorLayer)]
        kinds = Counter(self._by_net[n.id].kind if n.id in self._by_net else n.kind for n in b.nets)
        members = Counter(n.net_class for n in b.nets if n.net_class)
        classes = tuple(
            NetClassInfo(
                name=c.name,
                n_nets=members.get(c.name, 0),
                target_z_se=c.target_z_se,
                target_z_diff=c.target_z_diff,
            )
            for c in sorted(b.net_classes, key=lambda c: c.name)
        )
        intents = self._by_net.values()
        return BoardSummary(
            meta=BoardMetaView(
                name=b.meta.name,
                source_tool=b.meta.source_tool,
                source_file=b.meta.source_file,
                board_hash=self.raw_board_hash,
                physics_applied=self.physics_hash is not None,
            ),
            size_m=size,
            layer_count=len(copper),
            copper_layers=tuple(
                CopperLayerInfo(
                    id=layer.id,
                    name=layer.source_name or layer.user_name or layer.id,
                    z_m=layer.z_bottom,
                    thickness_m=layer.thickness,
                )
                for layer in copper
            ),
            total_thickness_m=b.stackup.total_thickness,
            nets=CountBy(total=len(b.nets), by_kind=dict(sorted(kinds.items()))),
            net_classes=classes,
            diff_pairs=len(b.diff_pairs),
            components=len(b.components),
            vias=CountBy(
                total=len(b.vias), by_kind=dict(sorted(Counter(v.kind for v in b.vias).items()))
            ),
            zones=ZoneCounts(
                count=len(b.zones), unfilled=sum(1 for z in b.zones if z.fill_state != "filled")
            ),
            intent=IntentBrief(
                high_speed_nets=sum(1 for i in intents if i.high_speed),
                rails=tuple(sorted(i.net for i in intents if i.kind == "power")),
                ground_nets=tuple(sorted(i.net for i in intents if i.kind == "ground")),
            ),
            diagnostics=_diag_digest(b),
        )

    # -- nets -------------------------------------------------------------------------------

    def _net_stats(self) -> dict[str, dict[str, Any]]:
        b = self.board
        stats: dict[str, dict[str, Any]] = {
            n.id: {"items": 0, "length": 0.0, "layers": set(), "vias": 0} for n in b.nets
        }
        for t in b.tracks:
            if t.net in stats:
                s = stats[t.net]
                s["items"] += 1
                s["length"] += sum(_seg_length(seg) for seg in t.path)
                s["layers"].add(t.layer)
        for v in b.vias:
            if v.net in stats:
                s = stats[v.net]
                s["items"] += 1
                s["vias"] += 1
        for p in b.pads:
            if p.net in stats:
                stats[p.net]["items"] += 1
        for z in b.zones:
            if z.net in stats:
                s = stats[z.net]
                s["items"] += 1
                s["layers"].update(z.layers)
        return stats

    def _info(self, net_id: str, st: dict[str, Any]) -> NetInfo:
        net = next(n for n in self.board.nets if n.id == net_id)
        it = self._by_net.get(net_id)
        partner = None
        for dp in self.board.diff_pairs:
            if dp.p_net == net_id:
                partner = dp.n_net
            elif dp.n_net == net_id:
                partner = dp.p_net
        tags = []
        if it is not None:
            if it.high_speed:
                tags.append("high_speed")
            if it.kind == "power":
                tags.append("rail")
            if it.kind == "ground":
                tags.append("ground")
        if partner is not None:
            tags.append("diff_pair")
        target = None
        if it is not None:
            target = (
                it.target_z_diff if partner is not None and it.target_z_diff else it.target_z_se
            )
            if target is None:
                target = it.target_z_diff
        return NetInfo(
            id=net_id,
            name=net_id,
            kind=(it.kind if it is not None else net.kind),
            net_class=net.net_class,
            diff_partner=partner,
            n_items=st["items"],
            length_m=st["length"],
            layers=tuple(sorted(st["layers"])),
            via_count=st["vias"],
            target_z=target,
            intent_tags=tuple(sorted(tags)),
        )

    def nets(self, q: NetQuery) -> Page[NetInfo]:
        stats = self._net_stats()
        infos = [self._info(n, stats[n]) for n in sorted(stats)]
        if q.query is not None:
            infos = [i for i in infos if fnmatch.fnmatchcase(i.name, q.query)]
        if q.kind is not None:
            infos = [i for i in infos if i.kind == q.kind]
        if q.net_class is not None:
            infos = [i for i in infos if i.net_class == q.net_class]
        if q.layer is not None:
            infos = [i for i in infos if q.layer in i.layers]
        if q.high_speed_only:
            infos = [i for i in infos if "high_speed" in i.intent_tags]
        return Page[NetInfo](**paginate(infos, q.page, "name"))

    def resolve_net(self, name_or_id: str) -> str:
        names = [n.id for n in self.board.nets]
        if name_or_id in names:
            return name_or_id
        folded = [n for n in names if n.lower() == name_or_id.lower()]
        if len(folded) == 1:
            return folded[0]
        if len(folded) > 1:
            raise ServiceError(
                "AMBIGUOUS_REF",
                f"Net {name_or_id!r} matches several nets.",
                path="/net",
                hint="Use the exact net name.",
                details={"candidates": sorted(folded)},
            )
        cands = close_matches(name_or_id, names) or sorted(names)[:5]
        raise ServiceError(
            "NOT_FOUND",
            f"No net named {name_or_id!r}.",
            path="/net",
            hint="Close matches: " + (", ".join(cands) or "none") + ". List nets with board.nets.",
            details={"candidates": cands},
        )

    def net(self, name_or_id: str) -> NetDetail:
        nid = self.resolve_net(name_or_id)
        b = self.board
        stats = self._net_stats()
        info = self._info(nid, stats[nid])
        per: dict[str, dict[str, Any]] = {}
        for t in b.tracks:
            if t.net == nid:
                d = per.setdefault(t.layer, {"len": 0.0, "w": set(), "n": 0})
                d["len"] += sum(_seg_length(s) for s in t.path)
                d["w"].add(t.width)
                d["n"] += 1
        padstacks = {p.id: p for p in b.padstacks}
        vias = []
        for v in sorted((v for v in b.vias if v.net == nid), key=lambda v: v.id):
            ps = padstacks.get(v.padstack)
            vias.append(
                ViaBrief(
                    id=v.id,
                    kind=v.kind,
                    span=(v.start_layer, v.end_layer),
                    drill_m=ps.drill.diameter if ps is not None and ps.drill else None,
                )
            )
        pads = sorted(
            (p for p in b.pads if p.net == nid), key=lambda p: (p.component or "", p.number, p.id)
        )
        pad_briefs = []
        for p in pads:
            ps = padstacks.get(p.padstack)
            layer = next((pl.layer for pl in ps.layers if pl.pad is not None), None) if ps else None
            pad_briefs.append(PadBrief(refdes=p.component or "", pin=p.number, layer=layer))
        it = self._by_net.get(nid)
        intent: dict[str, Any] = {}
        if it is not None:
            intent = {
                "rise_time_s": it.rise_time,
                "target_z_se_ohm": it.target_z_se,
                "target_z_diff_ohm": it.target_z_diff,
                "tolerance": it.z_tolerance,
                "high_speed": it.high_speed,
            }
        return NetDetail(
            info=info,
            per_layer=tuple(
                LayerRun(
                    layer=lid,
                    length_m=d["len"],
                    widths_m=tuple(sorted(d["w"]))[:10],
                    segments=d["n"],
                )
                for lid, d in sorted(per.items())
            ),
            vias=tuple(vias[:_MAX_VIAS]),
            pads=tuple(pad_briefs[:_MAX_PADS]),
            intent=intent,
            truncated=len(vias) > _MAX_VIAS or len(pads) > _MAX_PADS,
        )

    # -- stackup ----------------------------------------------------------------------------

    def stackup(self, f: float | None = None) -> StackupView:
        freq = DEFAULT_FREQUENCY_HZ if f is None else float(f)
        st = self.board.stackup
        mats = {m.name: m for m in st.materials}
        out = []
        for layer in st.layers:
            dk = df = sigma = None
            rough = None
            mat = mats.get(layer.material)
            if (
                isinstance(layer, DielectricLayer | MaskLayer)
                and mat is not None
                and mat.kind == "dielectric"
            ):
                eps = complex(eps_complex(mat.model, [freq])[0])
                dk = float(eps.real)
                df = float(-eps.imag / eps.real) if eps.real else 0.0
            if isinstance(layer, ConductorLayer):
                if mat is not None and mat.kind == "conductor":
                    sigma = mat.conductivity
                rough = layer.roughness_top.kind
            out.append(
                StackLayerView(
                    id=layer.id,
                    kind=layer.kind,
                    name=getattr(layer, "source_name", None),
                    z_bottom_m=layer.z_bottom,
                    z_top_m=layer.z_top,
                    thickness_m=layer.thickness,
                    material=layer.material,
                    dk=dk,
                    df=df,
                    conductivity_s_per_m=sigma,
                    roughness=rough,
                )
            )
        warnings = []
        if not st.known:
            warnings.append("The source had no stackup; a default stackup is used.")
        source = (
            "sidecar" if self.physics_hash is not None else ("board" if st.known else "default")
        )
        return StackupView(
            layers=tuple(out),
            total_thickness_m=st.total_thickness,
            frequency_hz=freq,
            physics_source=source,  # type: ignore[arg-type]
            warnings=tuple(warnings),
        )

    def geometry(self) -> Any:
        if self._geometry is None:
            if self._geometry_factory is not None:
                self._geometry = self._geometry_factory(self.board)
            else:
                from oa_geometry import BoardGeometry

                self._geometry = BoardGeometry.build(self.board)
        return self._geometry

    # -- stored setups ------------------------------------------------------------------------

    def setups(self) -> tuple[SetupSummary, ...]:
        found: dict[str, AnalysisSetup] = dict(self._memory_setups)
        if self.setups_dir is not None and self.setups_dir.is_dir():
            from oa_model.setup import load_setup

            for p in sorted([*self.setups_dir.glob("*.yaml"), *self.setups_dir.glob("*.yml")]):
                try:
                    s = load_setup(p)
                except Exception:  # unreadable setups are reported by validate_setup
                    continue
                found.setdefault(s.name, s)
        return tuple(
            SetupSummary(name=n, analysis=s.analysis, description=s.description)
            for n, s in sorted(found.items())
        )

    def setup(self, name: str) -> AnalysisSetup:
        if name in self._memory_setups:
            return self._memory_setups[name]
        if self.setups_dir is not None:
            from oa_model.setup import load_setup

            for ext in (".yaml", ".yml"):
                p = self.setups_dir / f"{name}{ext}"
                if p.is_file():
                    return load_setup(p)
        names = [s.name for s in self.setups()]
        cands = close_matches(name, names)
        raise ServiceError(
            "NOT_FOUND",
            f"No setup named {name!r}.",
            path="/name",
            hint="Stored setups: " + (", ".join(names) or "none") + ".",
            details={"candidates": cands},
        )

    def put_setup(self, setup: AnalysisSetup) -> SetupPutOut:
        import hashlib

        if self.setups_dir is None:
            self._memory_setups[setup.name] = setup
            blob = setup.model_dump_json().encode("utf-8")
            return SetupPutOut(
                name=setup.name,
                path=f"memory:{setup.name}",
                sha256=hashlib.sha256(blob).hexdigest(),
            )
        self.setups_dir.mkdir(parents=True, exist_ok=True)
        path = self.setups_dir / f"{setup.name}.yaml"
        backup = None
        if path.exists():
            bdir = self.setups_dir.parent.parent / ".oa" / "backups"
            bdir.mkdir(parents=True, exist_ok=True)
            backup_path = (
                bdir / f"{setup.name}.{hashlib.sha1(path.read_bytes()).hexdigest()[:8]}.yaml"
            )
            backup_path.write_bytes(path.read_bytes())
            backup = str(backup_path)
        doc = setup.model_dump(mode="json", by_alias=True)
        text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
        tmp = path.with_suffix(".yaml.tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        tmp.replace(path)
        return SetupPutOut(
            name=setup.name,
            path=str(path),
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            backup=backup,
        )
