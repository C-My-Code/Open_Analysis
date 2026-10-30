# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="arg-type,assignment,attr-defined,call-overload,index,return-value"
# mypy: disable-error-code="no-any-return,unused-ignore,import-untyped,union-attr,var-annotated"
"""Net topology (graph of pads, vias, junctions), routed segments and copper islands."""

from __future__ import annotations

import heapq
import itertools
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import shapely
from shapely import STRtree

from oa_geometry._fastcopy import replace
from oa_geometry.errors import UnknownItem
from oa_geometry.models import Island, NetTopology, RoutedSegment, Stub, TopoEdge, TopoNode
from oa_geometry.segs import JOIN_EPS, Piece, build_pieces, reverse_seg
from oa_geometry.vias import pad_copper_layers, via_copper_layers, via_stubs
from oa_model.index import ItemRef

if TYPE_CHECKING:
    from oa_geometry.geometry import BoardGeometry

__all__ = ["net_islands", "net_pieces", "net_segments", "net_topology"]

CLUSTER_GRID = 1e-8
"""Grid used to merge free track ends into one node [m] (10 nm, ten times the model grid)."""


def _check_net(geo: BoardGeometry, net: str) -> None:
    try:
        geo.idx.net(net)
    except KeyError:
        raise UnknownItem(f"unknown net {net!r}", path=net) from None


def all_pieces(geo: BoardGeometry) -> tuple[Piece, ...]:
    """Every routed piece of the board (cached)."""
    return geo._memoized("pieces", lambda: build_pieces(geo))  # type: ignore[no-any-return]


def pieces_by_net(geo: BoardGeometry) -> dict[str, list[Piece]]:
    def make() -> dict[str, list[Piece]]:
        out: dict[str, list[Piece]] = defaultdict(list)
        for p in all_pieces(geo):
            out[p.seg.net].append(p)
        return dict(out)

    return geo._memoized("pieces_by_net", make)  # type: ignore[no-any-return]


def net_pieces(geo: BoardGeometry, net: str) -> list[Piece]:
    _check_net(geo, net)
    return pieces_by_net(geo).get(net, [])


def _natural(s: str) -> list[object]:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


@dataclass
class _Topo:
    model: NetTopology
    dist: dict[str, float]
    seg_nodes: dict[str, tuple[str, str]] = field(default_factory=dict)


def _contacts(
    geo: BoardGeometry, net: str, layer: str
) -> tuple[STRtree, list[tuple[str, str, tuple[float, float]]]]:
    """Pad/via primitives of `net` on `layer` as (tree, [(kind, id, center)])."""
    idx = geo.strtree(layer)
    geoms: list[object] = []
    info: list[tuple[str, str, tuple[float, float]]] = []
    for i in geo._copper[layer].net_indices().get(net, ()):
        ref = idx.ref(i)
        if ref.kind == "pad":
            geoms.append(idx.geometry(i))
            info.append(("pad", ref.id, geo.idx.pad(ref.id).position))
        elif ref.kind == "via":
            geoms.append(idx.geometry(i))
            info.append(("via", ref.id, geo.idx.via(ref.id).position))
    arr = np.empty(len(geoms), dtype=object)
    arr[:] = geoms
    return STRtree(arr), info


def _build(geo: BoardGeometry, net: str, root: str | None) -> _Topo:
    _check_net(geo, net)
    board = geo.board
    pieces = pieces_by_net(geo).get(net, [])
    pads = [p for p in board.pads if p.net == net]
    vias = [v for v in board.vias if v.net == net]

    nodes: dict[str, TopoNode] = {}
    pt_nodes: dict[tuple[str, int, int], str] = {}
    n_pt = 0

    def add_node(node: TopoNode) -> str:
        nodes.setdefault(node.id, node)
        return node.id

    for p in pads:
        ps = geo.idx.padstack(p.padstack)
        lay = pad_copper_layers(ps)
        add_node(
            TopoNode(
                id=f"pad:{p.id}",
                kind="pad",
                position=p.position,
                layer=lay[0] if lay else None,
                ref=p.id,
            )
        )
    edges: list[TopoEdge] = []
    via_layer_nodes: dict[str, list[tuple[str, str]]] = {}
    for v in vias:
        ps = geo.idx.padstack(v.padstack)
        lays = via_copper_layers(geo.idx, v, ps)
        chain: list[tuple[str, str]] = []
        for lid in lays:
            nid = add_node(
                TopoNode(
                    id=f"via:{v.id}@{lid}", kind="via", position=v.position, layer=lid, ref=v.id
                )
            )
            chain.append((lid, nid))
        via_layer_nodes[v.id] = chain
        for k, ((l0, n0), (l1, n1)) in enumerate(itertools.pairwise(chain)):
            z0, z1 = geo.conductor(l0), geo.conductor(l1)
            length = abs((z0.z_bottom + z0.z_top) / 2 - (z1.z_bottom + z1.z_top) / 2)
            edges.append(TopoEdge(id=f"barrel:{v.id}:{k}", kind="via", a=n0, b=n1, length=length))

    contacts: dict[str, tuple[STRtree, list[tuple[str, str, tuple[float, float]]]]] = {}

    def node_at(layer: str, pt: tuple[float, float]) -> str:
        nonlocal n_pt
        if layer not in contacts:
            contacts[layer] = _contacts(geo, net, layer)
        tree, info = contacts[layer]
        if info:
            hits = tree.query(shapely.points(pt), predicate="dwithin", distance=JOIN_EPS)
            if len(hits):
                best = min(
                    (int(h) for h in hits),
                    key=lambda h: (math.hypot(pt[0] - info[h][2][0], pt[1] - info[h][2][1]), h),
                )
                kind, ident, _ = info[best]
                if kind == "pad":
                    return f"pad:{ident}"
                nid = f"via:{ident}@{layer}"
                if nid in nodes:
                    return nid
        key = (layer, round(pt[0] / CLUSTER_GRID), round(pt[1] / CLUSTER_GRID))
        if key not in pt_nodes:
            n_pt += 1
            # model_construct: the arguments are computed here, already of the field types
            pt_nodes[key] = add_node(
                TopoNode.model_construct(
                    id=f"pt{n_pt}",
                    kind="end",
                    position=(float(pt[0]), float(pt[1])),
                    layer=layer,
                    ref=None,
                )
            )
        return pt_nodes[key]

    seg_nodes: dict[str, tuple[str, str]] = {}
    for pc in pieces:
        a = node_at(pc.seg.layer, pc.start)
        b = node_at(pc.seg.layer, pc.end)
        seg_nodes[pc.seg.id] = (a, b)
        edges.append(
            TopoEdge.model_construct(
                id=pc.seg.id,
                kind="segment",
                a=a,
                b=b,
                length=float(pc.seg.length),
                layer=pc.seg.layer,
                seg_id=pc.seg.id,
                s_start=0.0,
            )
        )

    # zone connections: nodes whose position lies in a same-net zone fill on the node's layer
    zone_members: dict[tuple[str, str], list[str]] = defaultdict(list)
    for nid, node in nodes.items():
        layers = [node.layer] if node.layer else []
        if node.kind == "pad" and node.ref:
            layers = list(pad_copper_layers(geo.idx.padstack(geo.idx.pad(node.ref).padstack)))
        for lid in layers:
            if lid not in geo._copper:
                continue
            tree = geo.strtree(lid)
            px, py = node.position
            # the envelope of the round buffer is the point grown by the radius
            box = shapely.box(px - JOIN_EPS, py - JOIN_EPS, px + JOIN_EPS, py + JOIN_EPS)
            for i in tree.query_indices(box):
                ref = tree.ref(int(i))
                if ref.kind == "zone" and tree.net(int(i)) == net:
                    zone_members[(ref.id, lid)].append(nid)
    for (zid, lid), members in sorted(zone_members.items()):
        uniq = list(dict.fromkeys(members))
        if len(uniq) < 2:
            continue
        hub = f"zone:{zid}@{lid}"
        centre = geo.idx.zone(zid).outline.exterior.array.mean(axis=0)
        add_node(
            TopoNode(
                id=hub,
                kind="zone",
                position=(float(centre[0]), float(centre[1])),
                layer=lid,
                ref=zid,
            )
        )
        for k, m in enumerate(uniq):
            edges.append(
                TopoEdge(id=f"zone:{zid}@{lid}:{k}", kind="zone", a=hub, b=m, length=0.0, layer=lid)
            )

    # node kinds from segment degree
    deg: dict[str, int] = defaultdict(int)
    for e in edges:
        if e.kind == "segment":
            deg[e.a] += 1
            deg[e.b] += 1
    counters: dict[str, int] = defaultdict(int)
    rename: dict[str, str] = {}
    final_nodes: list[TopoNode] = []
    for nid, node in nodes.items():
        if nid.startswith("pt"):
            d = deg[nid]
            kind = "end" if d <= 1 else ("vertex" if d == 2 else "junction")
            prefix = {"end": "e", "vertex": "v", "junction": "j"}[kind]
            counters[prefix] += 1
            new = f"{prefix}{counters[prefix]}"
            rename[nid] = new
            final_nodes.append(replace(node, id=new, kind=kind))
        else:
            final_nodes.append(node)
    if rename:
        edges = [replace(e, a=rename.get(e.a, e.a), b=rename.get(e.b, e.b)) for e in edges]
        seg_nodes = {k: (rename.get(a, a), rename.get(b, b)) for k, (a, b) in seg_nodes.items()}

    # graph, root, distances
    adj: dict[str, dict[str, float]] = {n.id: {} for n in final_nodes}
    for e in edges:  # parallel edges: the shortest one
        for na, nb in ((e.a, e.b), (e.b, e.a)):
            row = adj.setdefault(na, {})
            if nb not in row or e.length < row[nb]:
                row[nb] = e.length
    root_id = _pick_root(geo, final_nodes, pads, root, net)
    dist: dict[str, float] = {}
    order = [root_id] if root_id else []
    order += [n.id for n in final_nodes if n.id != root_id]
    for start in order:
        if start in dist:
            continue
        for k, v in _dijkstra(adj, start).items():
            dist.setdefault(k, v)
    edges = [replace(e, s_start=dist.get(e.a, 0.0)) for e in edges]
    stubs = _stubs(geo, final_nodes, edges, via_layer_nodes, vias)
    model = NetTopology(
        net=net, root=root_id, nodes=tuple(final_nodes), edges=tuple(edges), stubs=tuple(stubs)
    )
    return _Topo(model=model, dist=dist, seg_nodes=seg_nodes)


def _dijkstra(adj: dict[str, dict[str, float]], source: str) -> dict[str, float]:
    """Shortest path lengths from ``source`` (the algorithm of
    ``networkx.single_source_dijkstra_path_length``, without building a graph object)."""
    dist: dict[str, float] = {}
    seen: dict[str, float] = {source: 0}  # type: ignore[dict-item]
    count = itertools.count(1)
    heap: list[tuple[float, int, str]] = [(0, 0, source)]  # type: ignore[list-item]
    while heap:
        d, _, v = heapq.heappop(heap)
        if v in dist:
            continue
        dist[v] = d
        for u, w in adj[v].items():
            vu = d + w
            if u not in dist and (u not in seen or vu < seen[u]):
                seen[u] = vu
                heapq.heappush(heap, (vu, next(count), u))
    return dist


def _pick_root(
    geo: BoardGeometry, nodes: list[TopoNode], pads: list[Any], root: str | None, net: str
) -> str | None:
    if root is not None:
        for cand in (f"pad:{root}", root):
            if any(n.id == cand for n in nodes):
                return cand
        for n in nodes:
            if n.id.startswith(f"via:{root}@"):
                return n.id
        raise UnknownItem(f"root {root!r} is not a pad or via of net {net!r}", path=root)
    if pads:
        first = min(pads, key=lambda p: (_natural(p.component or ""), _natural(p.number), p.id))
        return f"pad:{first.id}"
    for n in nodes:
        if n.kind == "via":
            return n.id
    ends = [n for n in nodes if n.kind == "end"]
    if ends:
        return min(ends, key=lambda n: (n.position[0], n.position[1])).id
    return nodes[0].id if nodes else None


def _stubs(
    geo: BoardGeometry,
    nodes: list[TopoNode],
    edges: list[TopoEdge],
    via_layer_nodes: dict[str, list[tuple[str, str]]],
    vias: list[Any],
) -> list[Stub]:
    inc: dict[str, list[TopoEdge]] = defaultdict(list)
    for e in edges:
        inc[e.a].append(e)
        inc[e.b].append(e)
    kinds = {n.id: n.kind for n in nodes}
    out: list[Stub] = []
    for n in nodes:
        if n.kind != "end":
            continue
        cur, prev_edge = n.id, None
        walked: list[TopoEdge] = []
        length = 0.0
        while True:
            nxt = [e for e in inc[cur] if e is not prev_edge and e.kind == "segment"]
            if prev_edge is None and len(nxt) != 1:
                break
            if prev_edge is not None and (kinds[cur] != "vertex" or len(nxt) != 1):
                break
            e = nxt[0]
            walked.append(e)
            length += e.length
            cur = e.b if e.a == cur else e.a
            prev_edge = e
            if kinds[cur] != "vertex":
                break
        if not walked:
            continue
        kind: Literal["branch", "pad_stub"] = "branch" if kinds[cur] == "junction" else "pad_stub"
        out.append(
            Stub(
                kind=kind,
                end_node=n.id,
                from_node=cur,
                length=length,
                edges=tuple(e.id for e in walked),
            )
        )
    used_nodes = {e.a for e in edges if e.kind != "via"} | {e.b for e in edges if e.kind != "via"}
    for v in vias:
        chain = via_layer_nodes.get(v.id, [])
        used = [(lid, nid) for lid, nid in chain if nid in used_nodes]
        if not used:
            continue
        top, bottom = via_stubs(geo.board, v, [lid for lid, _ in used])
        used_ix = [i for i, (_, nid) in enumerate(chain) if nid in used_nodes]
        for length, side in ((top, "top"), (bottom, "bottom")):
            if length <= JOIN_EPS:
                continue
            if side == "top":
                beyond = list(range(min(used_ix)))[::-1]
                from_n = chain[min(used_ix)][1]
                end_n = chain[0][1]
            else:
                beyond = list(range(max(used_ix), len(chain) - 1))
                from_n = chain[max(used_ix)][1]
                end_n = chain[-1][1]
            out.append(
                Stub(
                    kind="via_stub",
                    end_node=end_n,
                    from_node=from_n,
                    length=length,
                    edges=tuple(f"barrel:{v.id}:{i}" for i in beyond),
                )
            )
    return out


def net_topology(geo: BoardGeometry, net: str, root: str | None = None) -> NetTopology:
    """Topology of `net` (memoized per root)."""
    return _topo(geo, net, root).model


def _topo(geo: BoardGeometry, net: str, root: str | None) -> _Topo:
    return geo._memoized(("topo", net, root), lambda: _build(geo, net, root))  # type: ignore[no-any-return]


def net_segments(
    geo: BoardGeometry, net: str, root: str | None = None
) -> tuple[RoutedSegment, ...]:
    """Segments of `net` oriented away from the root, with ``s_start`` set, ordered by it."""
    return tuple(p.seg for p in oriented_pieces(geo, net, root))


def oriented_pieces(geo: BoardGeometry, net: str, root: str | None = None) -> list[Piece]:
    """Pieces oriented away from the topology root; ``seg.s_start`` is set."""
    from oa_geometry.segs import reverse_piece

    def make() -> list[Piece]:
        topo = _topo(geo, net, root)
        out: list[tuple[float, str, Piece]] = []
        for pc in net_pieces(geo, net):
            a, b = topo.seg_nodes[pc.seg.id]
            da, db = topo.dist.get(a, 0.0), topo.dist.get(b, 0.0)
            piece = pc if da <= db else reverse_piece(pc)
            s = min(da, db)
            piece = Piece(
                seg=replace(piece.seg, s_start=s),
                pts=piece.pts,
                line=piece.line,
                track=piece.track,
            )
            out.append((s, pc.seg.id, piece))
        out.sort(key=lambda t: (t[0], _natural(t[1])))
        return [t[2] for t in out]

    return geo._memoized(("oriented", net, root), make)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------------------------
# Islands
# ---------------------------------------------------------------------------------------------


class _UF:
    def __init__(self) -> None:
        self.p: dict[tuple[str, int], tuple[str, int]] = {}

    def find(self, x: tuple[str, int]) -> tuple[str, int]:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: tuple[str, int], b: tuple[str, int]) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def net_islands(geo: BoardGeometry, net: str) -> tuple[Island, ...]:
    """Galvanically separate copper groups of `net`.

    Copper of the net on one layer forms connected parts; parts on different layers join where a
    via of the net (or a plated through-hole pad) has copper on both layers at its position.
    """
    return geo._memoized(("islands", net), lambda: _islands(geo, net))  # type: ignore[no-any-return]


def _islands(geo: BoardGeometry, net: str) -> tuple[Island, ...]:
    _check_net(geo, net)
    uf = _UF()
    parts: dict[str, list[shapely.Polygon]] = {}
    trees: dict[str, STRtree] = {}
    members: dict[tuple[str, int], list[ItemRef]] = defaultdict(list)
    for lid in geo.layer_ids():
        mp = geo.copper(lid, net)
        if mp.is_empty:
            continue
        plist = list(mp.geoms)
        parts[lid] = plist
        arr = np.empty(len(plist), dtype=object)
        arr[:] = plist
        trees[lid] = STRtree(arr)
        for i in range(len(plist)):
            uf.find((lid, i))
        idx = geo.strtree(lid)
        for i in range(len(idx)):
            if idx.net(i) != net:
                continue
            rp = shapely.point_on_surface(idx.geometry(i))
            hit = trees[lid].query(rp, predicate="intersects")
            if len(hit):
                members[(lid, int(hit[0]))].append(idx.ref(i))

    def part_at(lid: str, pos: tuple[float, float]) -> tuple[str, int] | None:
        if lid not in trees:
            return None
        hit = trees[lid].query(shapely.points(pos), predicate="intersects")
        return (lid, int(hit[0])) if len(hit) else None

    for v in geo.board.vias:
        if v.net != net:
            continue
        lays = via_copper_layers(geo.idx, v, geo.idx.padstack(v.padstack))
        found = [part_at(lid, v.position) for lid in lays]
        keep = [f for f in found if f is not None]
        for a, b in itertools.pairwise(keep):
            uf.union(a, b)
    for p in geo.board.pads:
        if p.net != net:
            continue
        ps = geo.idx.padstack(p.padstack)
        if ps.drill is None or ps.drill.kind != "plated":
            continue
        found = [part_at(lid, p.position) for lid in pad_copper_layers(ps)]
        keep = [f for f in found if f is not None]
        for a, b in itertools.pairwise(keep):
            uf.union(a, b)

    groups: dict[tuple[str, int], list[tuple[str, int]]] = defaultdict(list)
    for key in list(uf.p):
        groups[uf.find(key)].append(key)
    built: list[tuple[float, tuple[float, float, float, float], Island]] = []
    for gkeys in groups.values():
        polys = [parts[lid][i] for lid, i in gkeys]
        area = float(sum(p.area for p in polys))
        bbox = shapely.total_bounds(np.array(polys, dtype=object))
        refs: dict[ItemRef, None] = {}
        for key in sorted(gkeys):
            for r in members.get(key, []):
                refs.setdefault(r, None)
        layers = sorted({lid for lid, _ in gkeys}, key=lambda s: int(s[1:]))
        isl = Island(
            id="",
            net=net,
            layers=tuple(layers),
            items=tuple(refs),
            area=area,
            bbox=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
        )
        built.append((-area, isl.bbox[:4], isl))
    built.sort(key=lambda t: (t[0], t[1]))
    return tuple(replace(t[2], id=f"{net}#{k}") for k, t in enumerate(built))


__all__ += ["oriented_pieces", "pieces_by_net", "reverse_seg"]
