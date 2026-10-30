# SPDX-License-Identifier: GPL-3.0-or-later
"""Semantic board validation (board-model.md section 13).

Every finding is a structured `Diagnostic`: code, severity, message, item (a stable key such as an
item id, net name or refdes), JSON-pointer `path` into the board document, and a `hint`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from typing import Literal, NamedTuple

import numpy as np
import numpy.typing as npt

from oa_model._base import GEOM_EPS
from oa_model.board import Board
from oa_model.common import Diagnostic
from oa_model.copper import Zone
from oa_model.errors import BoardValidationError
from oa_model.geometry import ArcSeg, PathSeg, Polygon, signed_area
from oa_model.padstack import PadShape, PolygonShape
from oa_model.stackup import ConductorLayer, DielectricLayer, MaskLayer

Arr = npt.NDArray[np.float64]


class RingKernels(NamedTuple):
    """Optional compiled replacements for the ring predicates below (same booleans). oa-model
    cannot import the compiled core, so a caller above it passes the kernels in."""

    self_intersects: Callable[[Arr], bool]
    all_inside: Callable[[Arr, Arr], bool]


_MAX_RING_CHECK = 600  # rings larger than this skip the O(n^2) self-intersection test


def _diag(
    code: str,
    message: str,
    path: str,
    hint: str,
    *,
    severity: Literal["info", "warning", "error"] = "error",
    item: str | None = None,
    layer: str | None = None,
) -> Diagnostic:
    return Diagnostic(
        severity=severity,
        code=code,
        message=message,
        path=path,
        hint=hint,
        item=item[:200] if item else None,
        layer=layer,
    )


def _ring_self_intersects(a: Arr) -> bool:
    n = a.shape[0]
    p = a
    q = np.roll(a, -1, axis=0)

    def orient(u: Arr, v: Arr, w: Arr) -> Arr:
        return (v[..., 0] - u[..., 0]) * (w[..., 1] - u[..., 1]) - (v[..., 1] - u[..., 1]) * (
            w[..., 0] - u[..., 0]
        )

    pi, qi = p[:, None, :], q[:, None, :]
    pj, qj = p[None, :, :], q[None, :, :]
    o1 = orient(pi, qi, pj)
    o2 = orient(pi, qi, qj)
    o3 = orient(pj, qj, pi)
    o4 = orient(pj, qj, qi)
    cross = (o1 * o2 < 0) & (o3 * o4 < 0)
    idx = np.arange(n)
    d = np.abs(idx[:, None] - idx[None, :])
    adjacent = (d <= 1) | (d == n - 1)
    return bool(np.any(cross & ~adjacent))


def _points_inside(ring: Arr, pts: Arr) -> Arr:
    """Even-odd point-in-polygon for many points."""
    x, y = pts[:, 0][:, None], pts[:, 1][:, None]
    x1, y1 = ring[:, 0][None, :], ring[:, 1][None, :]
    x2, y2 = np.roll(ring[:, 0], -1)[None, :], np.roll(ring[:, 1], -1)[None, :]
    with np.errstate(divide="ignore", invalid="ignore"):
        xint = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
    hit = ((y1 > y) != (y2 > y)) & (x < xint)
    return np.asarray(hit.sum(axis=1) % 2 == 1)


def _check_polygon(
    poly: Polygon,
    path: str,
    item: str | None,
    layer: str | None,
    kernels: RingKernels | None = None,
) -> Iterator[Diagnostic]:
    rings = [("exterior", poly.exterior)] + [(f"holes/{i}", h) for i, h in enumerate(poly.holes)]
    for name, ring in rings:
        rp = f"{path}/{name}"
        a = ring.array
        if len(ring) < 3:
            yield _diag(
                "MODEL.POLY_INVALID",
                f"ring has {len(ring)} points; at least 3 are required",
                rp,
                "supply a ring with at least three distinct points",
                item=item,
                layer=layer,
            )
            continue
        area = signed_area(ring)
        if name == "exterior" and area < 0:
            yield _diag(
                "MODEL.POLY_ORIENT",
                "exterior ring is clockwise; it must be counter-clockwise",
                rp,
                "reverse the ring or call Polygon.normalized()",
                item=item,
                layer=layer,
            )
        elif name != "exterior" and area > 0:
            yield _diag(
                "MODEL.POLY_ORIENT",
                "hole ring is counter-clockwise; holes must be clockwise",
                rp,
                "reverse the ring or call Polygon.normalized()",
                item=item,
                layer=layer,
            )
        if len(ring) <= _MAX_RING_CHECK and (
            kernels.self_intersects(a) if kernels else _ring_self_intersects(a)
        ):
            yield _diag(
                "MODEL.POLY_INVALID",
                "ring is self-intersecting",
                rp,
                "re-union or repair the polygon before building the board",
                item=item,
                layer=layer,
            )
    ext = poly.exterior
    if len(ext) >= 3 and len(ext) <= _MAX_RING_CHECK:
        for i, h in enumerate(poly.holes):
            if len(h) >= 3 and not (
                kernels.all_inside(ext.array, h.array)
                if kernels
                else bool(_points_inside(ext.array, h.array).all())
            ):
                yield _diag(
                    "MODEL.POLY_INVALID",
                    "hole is not inside the exterior ring",
                    f"{path}/holes/{i}",
                    "drop the hole or fix its coordinates",
                    item=item,
                    layer=layer,
                )


def _shape_polygons(shape: PadShape | None) -> Iterator[tuple[Polygon, str]]:
    if isinstance(shape, PolygonShape):
        for i, p in enumerate(shape.polygons):
            yield p, f"/polygons/{i}"


def _all_polygons(board: Board) -> Iterator[tuple[Polygon, str, str | None, str | None]]:
    yield board.outline.polygon, "/outline/polygon", None, None
    for i, p in enumerate(board.outline.extra_boards):
        yield p, f"/outline/extra_boards/{i}", None, None
    for i, z in enumerate(board.zones):
        yield z.outline, f"/zones/{i}/outline", z.id, None
        for j, f in enumerate(z.fills):
            for k, p in enumerate(f.polygons):
                yield p, f"/zones/{i}/fills/{j}/polygons/{k}", z.id, f.layer
    for i, s in enumerate(board.shapes):
        yield s.polygon, f"/shapes/{i}/polygon", s.id, s.layer
    for i, c in enumerate(board.components):
        if c.courtyard is not None:
            yield c.courtyard, f"/components/{i}/courtyard", c.refdes, None
    for i, ps in enumerate(board.padstacks):
        for j, pl in enumerate(ps.layers):
            for field in ("pad", "antipad"):
                for p, sub in _shape_polygons(getattr(pl, field)):
                    yield p, f"/padstacks/{i}/layers/{j}/{field}{sub}", ps.id, pl.layer
        for field in ("mask_opening_top", "mask_opening_bottom"):
            for p, sub in _shape_polygons(getattr(ps, field)):
                yield p, f"/padstacks/{i}/{field}{sub}", ps.id, None


def _seg_end(s: PathSeg) -> tuple[float, float]:
    return s.end


def validate_board(
    board: Board, *, strict: bool = False, kernels: RingKernels | None = None
) -> tuple[Diagnostic, ...]:
    """Semantic checks beyond pydantic structure. With strict=True any 'error' raises
    BoardValidationError. Codes: MODEL.DUP_ID, DANGLING_REF, LAYER_KIND, VIA_SPAN, BACKDRILL,
    POLY_ORIENT, POLY_INVALID, TRACK_PATH, DIFFPAIR, ZONE_FILL_LAYER, STACKUP_Z, OUTLINE."""
    out: list[Diagnostic] = []
    add = out.append
    layers = {layer.id: layer for layer in board.stackup.layers}
    conductors = [layer for layer in board.stackup.layers if isinstance(layer, ConductorLayer)]
    cidx = {layer.id: layer.index for layer in conductors}
    n_cu = len(conductors)
    net_names = {n.id for n in board.nets}

    # ---- duplicates -------------------------------------------------------------------------
    def dups(keys: list[str], label: str, base: str) -> None:
        for k, c in Counter(keys).items():
            if c > 1:
                first = keys.index(k)
                second = keys.index(k, first + 1)
                add(
                    _diag(
                        "MODEL.DUP_ID",
                        f"{label} {k!r} appears {c} times",
                        f"{base}/{second}",
                        f"make every {label} unique",
                        item=k,
                    )
                )

    items: list[tuple[str, str, str]] = []  # (id, collection, index)
    for coll in ("pads", "vias", "tracks", "zones", "shapes"):
        for i, obj in enumerate(getattr(board, coll)):
            items.append((obj.id, coll, str(i)))
    seen_items: dict[str, str] = {}
    for iid, coll, ix in items:
        if iid in seen_items:
            add(
                _diag(
                    "MODEL.DUP_ID",
                    f"item id {iid!r} is used twice (also at {seen_items[iid]})",
                    f"/{coll}/{ix}/id",
                    "item ids must be unique across pads, vias, tracks, zones and shapes",
                    item=iid,
                )
            )
        else:
            seen_items[iid] = f"/{coll}/{ix}"
    dups([n.id for n in board.nets], "net name", "/nets")
    dups([c.name for c in board.net_classes], "net class", "/net_classes")
    dups([d.id for d in board.diff_pairs], "diff pair id", "/diff_pairs")
    dups([p.id for p in board.padstacks], "padstack id", "/padstacks")
    dups([c.refdes for c in board.components], "refdes", "/components")
    dups([layer.id for layer in board.stackup.layers], "layer id", "/stackup/layers")
    dups([m.name for m in board.stackup.materials], "material name", "/stackup/materials")

    # ---- reference helpers ------------------------------------------------------------------
    def conductor_ref(layer_id: str, path: str, item: str | None) -> bool:
        """Check a layer reference. Returns True when it resolves to a conductor layer."""
        layer = layers.get(layer_id)
        if layer is None:
            add(
                _diag(
                    "MODEL.DANGLING_REF",
                    f"layer {layer_id!r} does not exist in the stackup",
                    path,
                    f"use one of: {', '.join(sorted(cidx))}",
                    item=item,
                )
            )
            return False
        if not isinstance(layer, ConductorLayer):
            add(
                _diag(
                    "MODEL.LAYER_KIND",
                    f"layer {layer_id!r} is a {layer.kind} layer, not copper",
                    path,
                    f"use a conductor layer id (L1..L{n_cu})",
                    item=item,
                    layer=layer_id,
                )
            )
            return False
        return True

    def net_ref(net: str | None, path: str, item: str) -> None:
        if net is not None and net not in net_names:
            add(
                _diag(
                    "MODEL.DANGLING_REF",
                    f"net {net!r} is not defined in board.nets",
                    path,
                    "add the net to board.nets or set the reference to null",
                    item=item,
                )
            )

    class_names = {c.name for c in board.net_classes}
    for i, n in enumerate(board.nets):
        if n.net_class is not None and n.net_class not in class_names:
            add(
                _diag(
                    "MODEL.DANGLING_REF",
                    f"net class {n.net_class!r} of net {n.id!r} is not defined",
                    f"/nets/{i}/net_class",
                    "add the class to board.net_classes or clear net_class",
                    item=n.id,
                )
            )

    # ---- stackup ----------------------------------------------------------------------------
    mats = {m.name: m for m in board.stackup.materials}
    for i, layer in enumerate(board.stackup.layers):
        base = f"/stackup/layers/{i}"
        m = mats.get(layer.material)
        want = "conductor" if isinstance(layer, ConductorLayer) else "dielectric"
        if m is None:
            add(
                _diag(
                    "MODEL.DANGLING_REF",
                    f"material {layer.material!r} of layer {layer.id} is not in stackup.materials",
                    f"{base}/material",
                    "add the material to stackup.materials",
                    item=layer.id,
                )
            )
        elif m.kind != want:
            add(
                _diag(
                    "MODEL.DANGLING_REF",
                    f"material {layer.material!r} is a {m.kind} material but layer {layer.id} "
                    f"needs a {want} material",
                    f"{base}/material",
                    f"reference a {want} material",
                    item=layer.id,
                )
            )
        if abs((layer.z_top - layer.z_bottom) - layer.thickness) > GEOM_EPS:
            add(
                _diag(
                    "MODEL.STACKUP_Z",
                    f"layer {layer.id}: z_top - z_bottom != thickness",
                    base,
                    "set z_top = z_bottom + thickness",
                    item=layer.id,
                )
            )
    sl = board.stackup.layers
    for i in range(len(sl) - 1):
        if abs(sl[i].z_bottom - sl[i + 1].z_top) > GEOM_EPS:
            add(
                _diag(
                    "MODEL.STACKUP_Z",
                    f"gap or overlap between layers {sl[i].id} and {sl[i + 1].id}",
                    f"/stackup/layers/{i + 1}/z_top",
                    "layers must be contiguous: layers[i].z_bottom == layers[i+1].z_top",
                    item=sl[i].id,
                )
            )
    if sl:
        if abs(sl[-1].z_bottom) > GEOM_EPS:
            add(
                _diag(
                    "MODEL.STACKUP_Z",
                    "the bottom layer does not start at z = 0",
                    f"/stackup/layers/{len(sl) - 1}/z_bottom",
                    "z = 0 is the bottom surface of the bottom-most layer",
                    item=sl[-1].id,
                )
            )
        if abs(board.stackup.total_thickness - sl[0].z_top) > GEOM_EPS:
            add(
                _diag(
                    "MODEL.STACKUP_Z",
                    "total_thickness differs from layers[0].z_top",
                    "/stackup/total_thickness",
                    "set total_thickness to the z_top of the first layer",
                )
            )
    cu_seq = [layer for layer in sl if isinstance(layer, ConductorLayer)]
    di_seq = [layer for layer in sl if isinstance(layer, DielectricLayer)]
    groups: list[tuple[Sequence[ConductorLayer | DielectricLayer], str]] = [
        (cu_seq, "L"),
        (di_seq, "D"),
    ]
    for seq, prefix in groups:
        for k, lay in enumerate(seq, start=1):
            if lay.id != f"{prefix}{k}" or lay.index != k:
                add(
                    _diag(
                        "MODEL.STACKUP_Z",
                        f"layer {lay.id} (index {lay.index}) is out of sequence; "
                        f"expected {prefix}{k}",
                        "/stackup/layers",
                        f"number {prefix}-layers consecutively from the top",
                        item=layer.id,
                    )
                )
    masks = [layer for layer in sl if isinstance(layer, MaskLayer)]
    for mk in masks:
        idx = sl.index(mk)
        if (mk.id == "MT") != (idx == 0):
            add(
                _diag(
                    "MODEL.STACKUP_Z",
                    f"mask {mk.id} is at the wrong end of the stack",
                    f"/stackup/layers/{idx}",
                    "MT must be the first layer and MB the last",
                    item=mk.id,
                )
            )

    # ---- pads / vias / padstacks -----------------------------------------------------------
    pad_ids = {p.id for p in board.pads}
    refdes = {c.refdes for c in board.components}
    stack_ids = {p.id for p in board.padstacks}
    for i, p in enumerate(board.pads):
        net_ref(p.net, f"/pads/{i}/net", p.id)
        if p.padstack not in stack_ids:
            add(
                _diag(
                    "MODEL.DANGLING_REF",
                    f"padstack {p.padstack!r} of pad {p.id!r} is not defined",
                    f"/pads/{i}/padstack",
                    "add the padstack to board.padstacks",
                    item=p.id,
                )
            )
        if p.component is not None and p.component not in refdes:
            add(
                _diag(
                    "MODEL.DANGLING_REF",
                    f"component {p.component!r} of pad {p.id!r} is not defined",
                    f"/pads/{i}/component",
                    "add the component or set component to null",
                    item=p.id,
                )
            )
    for i, c in enumerate(board.components):
        for j, pid in enumerate(c.pads):
            if pid not in pad_ids:
                add(
                    _diag(
                        "MODEL.DANGLING_REF",
                        f"pad {pid!r} of component {c.refdes!r} does not exist",
                        f"/components/{i}/pads/{j}",
                        "remove the id or add the pad",
                        item=c.refdes,
                    )
                )
    for i, ps in enumerate(board.padstacks):
        for j, pl in enumerate(ps.layers):
            conductor_ref(pl.layer, f"/padstacks/{i}/layers/{j}/layer", ps.id)

    for i, v in enumerate(board.vias):
        net_ref(v.net, f"/vias/{i}/net", v.id)
        if v.padstack not in stack_ids:
            add(
                _diag(
                    "MODEL.DANGLING_REF",
                    f"padstack {v.padstack!r} of via {v.id!r} is not defined",
                    f"/vias/{i}/padstack",
                    "add the padstack to board.padstacks",
                    item=v.id,
                )
            )
        ok_s = conductor_ref(v.start_layer, f"/vias/{i}/start_layer", v.id)
        ok_e = conductor_ref(v.end_layer, f"/vias/{i}/end_layer", v.id)
        if not (ok_s and ok_e):
            continue
        s, e = cidx[v.start_layer], cidx[v.end_layer]
        span_msg = None
        if s > e:
            span_msg = "start_layer lies below end_layer"
        elif v.kind == "through" and not (s == 1 and e == n_cu):
            span_msg = f"through via must span L1..L{n_cu}"
        elif v.kind == "blind" and (s == 1) == (e == n_cu):
            span_msg = "blind via must reach exactly one outer layer"
        elif v.kind == "buried" and (s == 1 or e == n_cu):
            span_msg = "buried via must not touch an outer layer"
        elif v.kind == "micro" and e - s != 1:
            span_msg = "micro via must span two adjacent copper layers"
        if span_msg:
            add(
                _diag(
                    "MODEL.VIA_SPAN",
                    f"via {v.id!r}: {span_msg}",
                    f"/vias/{i}",
                    "fix start_layer/end_layer or the via kind",
                    item=v.id,
                )
            )
            continue
        sides: Counter[str] = Counter()
        for j, bd in enumerate(v.backdrills):
            bp = f"/vias/{i}/backdrills/{j}"
            sides[bd.side] += 1
            if not conductor_ref(bd.last_removed_layer, f"{bp}/last_removed_layer", v.id):
                continue
            k = cidx[bd.last_removed_layer]
            problem = None
            if not (s <= k <= e):
                problem = "last_removed_layer is outside the via span"
            elif bd.side == "top" and k >= e:
                problem = "a top backdrill may not remove the whole barrel"
            elif bd.side == "bottom" and k <= s:
                problem = "a bottom backdrill may not remove the whole barrel"
            elif bd.must_not_cut is not None:
                if not conductor_ref(bd.must_not_cut, f"{bp}/must_not_cut", v.id):
                    continue
                want_k = k + 1 if bd.side == "top" else k - 1
                if cidx[bd.must_not_cut] != want_k:
                    problem = "must_not_cut must be the layer next to last_removed_layer, inward"
            if problem:
                add(
                    _diag(
                        "MODEL.BACKDRILL",
                        f"via {v.id!r}: {problem}",
                        bp,
                        "check side, last_removed_layer and must_not_cut against the via span",
                        item=v.id,
                    )
                )
        for side, cnt in sides.items():
            if cnt > 1:
                add(
                    _diag(
                        "MODEL.BACKDRILL",
                        f"via {v.id!r} has {cnt} backdrills on the {side} side",
                        f"/vias/{i}/backdrills",
                        "keep at most one backdrill per side",
                        item=v.id,
                    )
                )

    # ---- copper -----------------------------------------------------------------------------
    for i, t in enumerate(board.tracks):
        net_ref(t.net, f"/tracks/{i}/net", t.id)
        conductor_ref(t.layer, f"/tracks/{i}/layer", t.id)
        for j in range(len(t.path) - 1):
            a, b = t.path[j].end, t.path[j + 1].start
            if abs(a[0] - b[0]) > GEOM_EPS or abs(a[1] - b[1]) > GEOM_EPS:
                add(
                    _diag(
                        "MODEL.TRACK_PATH",
                        f"track {t.id!r}: segment {j} ends at {a} but segment {j + 1} starts at {b}",
                        f"/tracks/{i}/path/{j + 1}/start",
                        "path segments must be contiguous; split the track or fix the points",
                        item=t.id,
                        layer=t.layer,
                    )
                )
        for j, seg in enumerate(t.path):
            if isinstance(seg, ArcSeg) and seg.start == seg.end:
                add(
                    _diag(
                        "MODEL.TRACK_PATH",
                        f"track {t.id!r}: arc {j} has identical start and end",
                        f"/tracks/{i}/path/{j}",
                        "use two arcs for a full circle",
                        severity="warning",
                        item=t.id,
                    )
                )
    for i, z in enumerate(board.zones):
        net_ref(z.net, f"/zones/{i}/net", z.id)
        _check_zone(z, i, conductor_ref, add)
    for i, s_ in enumerate(board.shapes):
        net_ref(s_.net, f"/shapes/{i}/net", s_.id)
        conductor_ref(s_.layer, f"/shapes/{i}/layer", s_.id)

    # ---- diff pairs -------------------------------------------------------------------------
    for i, d in enumerate(board.diff_pairs):
        for side, name in (("p_net", d.p_net), ("n_net", d.n_net)):
            if name not in net_names:
                add(
                    _diag(
                        "MODEL.DIFFPAIR",
                        f"diff pair {d.id!r}: {side} {name!r} is not a net of this board",
                        f"/diff_pairs/{i}/{side}",
                        "use an existing net name",
                        item=d.id,
                    )
                )
        if d.p_net == d.n_net:
            add(
                _diag(
                    "MODEL.DIFFPAIR",
                    f"diff pair {d.id!r}: p_net and n_net are both {d.p_net!r}",
                    f"/diff_pairs/{i}",
                    "a pair needs two different nets",
                    item=d.id,
                )
            )

    # ---- polygons and outline ---------------------------------------------------------------
    for poly, ppath, pitem, player in _all_polygons(board):
        out.extend(_check_polygon(poly, ppath, pitem, player, kernels))
    ext = board.outline.polygon.exterior
    if len(ext) < 3 or abs(signed_area(ext)) < GEOM_EPS * GEOM_EPS:
        add(
            _diag(
                "MODEL.OUTLINE",
                "board outline is missing or degenerate",
                "/outline/polygon/exterior",
                "close the board outline in the source; solvers that need it will refuse",
                severity="warning",
            )
        )

    result = tuple(out)
    if strict and any(d.severity == "error" for d in result):
        errs = [d for d in result if d.severity == "error"]
        raise BoardValidationError(
            f"{len(errs)} validation error(s); first: {errs[0].code} {errs[0].message}", result
        )
    return result


def _check_zone(
    z: Zone,
    i: int,
    conductor_ref: Callable[[str, str, str | None], bool],
    add: Callable[[Diagnostic], None],
) -> None:
    for j, lid in enumerate(z.layers):
        conductor_ref(lid, f"/zones/{i}/layers/{j}", z.id)
    for j, f in enumerate(z.fills):
        conductor_ref(f.layer, f"/zones/{i}/fills/{j}/layer", z.id)
        if f.layer not in z.layers:
            add(
                _diag(
                    "MODEL.ZONE_FILL_LAYER",
                    f"zone {z.id!r} has a fill on {f.layer!r}, which is not in zone.layers",
                    f"/zones/{i}/fills/{j}/layer",
                    "add the layer to zone.layers or drop the fill",
                    item=z.id,
                    layer=f.layer,
                )
            )
