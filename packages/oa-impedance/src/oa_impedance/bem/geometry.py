# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cross-section -> boundary panels for the 2D BEM.

The boundary set is the *noded* union of every conductor outline, every dielectric region
boundary and every collapsed (zero-thickness) conductor ribbon. Each noded piece is classified:

- inside a conductor: dropped;
- on a conductor outline: a conductor panel (outside medium = the zone it faces);
- on a ribbon: a ribbon conductor panel (zones on both sides);
- otherwise a dielectric interface (dropped when both sides have the same material).

Panel sizes follow ``s = clip(alpha * d, s_min, s_max)`` with ``d`` the distance to the nearest
other piece or to a piece end / sharp corner (corner grading, no tuning per geometry).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from itertools import pairwise
from typing import Any

import numpy as np
import numpy.typing as npt
import shapely  # type: ignore[import-untyped,unused-ignore]
from shapely.geometry import (  # type: ignore[import-untyped,unused-ignore]
    LineString,
    MultiLineString,
    Polygon,
)
from shapely.ops import unary_union  # type: ignore[import-untyped,unused-ignore]

from oa_model.xsection import (
    CrossSection,
    XsCircle,
    XsPlane,
    XsPolygon,
    XsRegion,
    XsSlab,
    XsTrapezoid,
)

F64 = npt.NDArray[np.float64]
I64 = npt.NDArray[np.int64]

__all__ = ["MeshParams", "Panels", "Problem", "build_problem"]

KIND_THICK, KIND_RIBBON, KIND_INTERFACE = 0, 1, 2
FACE_SIDE, FACE_TOP, FACE_BOTTOM = 0, 1, 2
_THIN_REL = 2e-3  # trapezoid thinner than this * width collapses to a ribbon
_SHARP = math.radians(25.0)


@dataclass(frozen=True)
class MeshParams:
    """Discretization controls. ``alpha`` is the panel-size/distance ratio (smaller = finer)."""

    alpha: float = 0.4
    gap_ratio: float = 1.0  # panel size / distance to another conductor, in units of alpha
    s_min_rel: float = 3e-4  # smallest panel / characteristic length
    s_max_rel: float = 1.5  # largest panel / characteristic length
    truncation: float = 20.0  # open boundaries cut at this many truncation lengths
    plane_relax: float = 1.0  # planes: faces of thin copper may use panels up to this * distance
    far_start: float = 3.0  # far field begins this many truncation lengths from the signals
    far_ratio: float = 0.3  # beyond it, panels at distance d are at least far_ratio * (d - start)
    window: bool = True  # truncation length from the signal cluster + stack height (not the window)
    min_feature_ratio: float = 0.05  # s_min <= this * smallest conductor thickness (WP-510)

    def scaled(self, factor: float) -> MeshParams:
        """A uniformly refined (factor > 1) or coarsened copy."""
        return replace(self, alpha=self.alpha / factor, s_min_rel=self.s_min_rel / factor)


@dataclass
class Panels:
    a: F64
    b: F64
    cond: I64  # conductor index, -1 for interface panels
    kind: I64
    zone1: I64  # zone the normal points into
    zone2: I64  # zone on the other side (-1 = metal)
    face: I64
    mid: F64 = field(init=False)
    length: F64 = field(init=False)
    tangent: F64 = field(init=False)
    normal: F64 = field(init=False)

    def __post_init__(self) -> None:
        d = self.b - self.a
        self.length = np.hypot(d[:, 0], d[:, 1])
        self.tangent = d / self.length[:, None]
        self.normal = np.stack([-self.tangent[:, 1], self.tangent[:, 0]], axis=1)
        self.mid = 0.5 * (self.a + self.b)

    @property
    def n(self) -> int:
        return int(self.a.shape[0])


@dataclass
class Problem:
    panels: Panels
    zone_models: list[object | None]  # index 0 = background; None -> constant background_dk
    background_dk: float
    cond_ids: list[str]
    cond_roles: list[str]
    cond_sigma: list[float]
    cond_area: list[float]
    cond_rough: list[tuple[Any, Any, Any]]  # (top, bottom, side)
    notes: list[str]
    h_char: float
    u_trunc: float


# ----------------------------------------------------------------------------- shapes


def _ring_pts(arr: F64) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in arr]


def _circle_poly(uc: float, zc: float, r: float, n: int = 360) -> Polygon:
    # equal-area radius: the n-gon then has the same area (and, to O(1/n^4), capacity) as the disc
    x = 2.0 * math.pi / n
    r_eq = r * math.sqrt(x / math.sin(x))
    th = np.linspace(0.0, 2.0 * math.pi, n, endpoint=False)
    return Polygon(np.stack([uc + r_eq * np.cos(th), zc + r_eq * np.sin(th)], axis=1))


def _group(cond: int, kind: int, pid: int) -> int:
    """Pieces of one conductor do not refine each other (opposite faces of a thin strip)."""
    return 10_000_000 + cond if cond >= 0 and kind != KIND_INTERFACE else pid


def _curvature_cap(sp: F64) -> float:
    """Largest panel length allowed by the curvature of the polyline ``sp`` (0.1 * radius)."""
    if len(sp) < 3:
        return math.inf
    v = np.diff(sp, axis=0)
    ln = np.hypot(v[:, 0], v[:, 1])
    ang = np.arctan2(v[:, 1], v[:, 0])
    turn = np.abs((np.diff(ang) + np.pi) % (2 * np.pi) - np.pi)
    ok = turn > 1e-3
    if not ok.any():
        return math.inf
    radius = 0.5 * (ln[:-1] + ln[1:])[ok] / turn[ok]
    return float(0.06 * np.min(radius))


def _u_extent(s: Any) -> list[float]:
    """Finite lateral extent [m] of a conductor shape (empty for an unbounded plane)."""
    if isinstance(s, XsTrapezoid):
        h = max(s.w_bottom, s.w_top) / 2
        return [s.u_center - h, s.u_center + h]
    if isinstance(s, XsCircle):
        return [s.u_center - s.radius, s.u_center + s.radius]
    if isinstance(s, XsPolygon):
        arr = s.polygon.exterior.array
        return [float(np.min(arr[:, 0])), float(np.max(arr[:, 0]))]
    return [v for v in (s.u_min, s.u_max) if v is not None]


def _curvature_caps(sp: F64) -> tuple[F64, F64]:
    """Arc length of every interior vertex of the polyline ``sp`` and the panel length (0.06 *
    local radius) allowed there; vertices of negligible turn are left out."""
    if len(sp) < 3:
        return np.zeros(0), np.zeros(0)
    v = np.diff(sp, axis=0)
    ln = np.hypot(v[:, 0], v[:, 1])
    ang = np.arctan2(v[:, 1], v[:, 0])
    turn = np.abs((np.diff(ang) + np.pi) % (2 * np.pi) - np.pi)
    ok = turn > 1e-3
    pos = np.concatenate([[0.0], np.cumsum(ln)])[1:-1]
    return pos[ok], 0.06 * (0.5 * (ln[:-1] + ln[1:]))[ok] / turn[ok]


def _plane_covers(xs: CrossSection, u_lo: float, u_hi: float) -> bool:
    """Whether the planes of one z level together span ``[u_lo, u_hi]`` (a reference exists)."""
    by_z: dict[float, list[tuple[float, float]]] = {}
    for c in xs.conductors:
        s = c.shape
        if isinstance(s, XsPlane):
            by_z.setdefault(round(s.z_bottom, 9), []).append(
                (
                    -math.inf if s.u_min is None else s.u_min,
                    math.inf if s.u_max is None else s.u_max,
                )
            )
    for ivs in by_z.values():
        reach = u_lo
        for a, b in sorted(ivs):
            if a > reach + 1e-12:
                break
            reach = max(reach, b)
        if reach >= u_hi:
            return True
    return False


def _z_bounds(s: Any) -> list[float]:
    """Finite z levels [m] of a conductor shape or dielectric region."""
    if isinstance(s, XsTrapezoid | XsPlane):
        return [s.z_bottom, s.z_bottom + s.thickness]
    if isinstance(s, XsCircle):
        return [s.z_center - s.radius, s.z_center + s.radius]
    if isinstance(s, XsSlab):
        return [s.z_bottom, s.z_top]
    arr = s.polygon.exterior.array
    return [float(np.min(arr[:, 1])), float(np.max(arr[:, 1]))]


def _zone_key(model: object | None, dkz: float, bg: float) -> tuple[str, float]:
    if model is None:
        return (f"const:{bg:.12g}", 1.0)
    return (repr(getattr(model, "model_dump", lambda: model)()), dkz)


def build_problem(xs: CrossSection, params: MeshParams) -> Problem:
    notes: list[str] = []
    cond_polys: list[Polygon | None] = []
    cond_ribbons: list[LineString | None] = []
    ids, roles, sigmas, areas, roughs = [], [], [], [], []

    # ---- extents (finite parts) for the characteristic length and truncation
    us: list[float] = []
    zs: list[float] = []
    for c in xs.conductors:
        s = c.shape
        if isinstance(s, XsTrapezoid):
            us += [
                s.u_center - max(s.w_bottom, s.w_top) / 2,
                s.u_center + max(s.w_bottom, s.w_top) / 2,
            ]
            zs += [s.z_bottom, s.z_bottom + s.thickness]
        elif isinstance(s, XsCircle):
            us += [s.u_center - s.radius, s.u_center + s.radius]
            zs += [s.z_center - s.radius, s.z_center + s.radius]
        elif isinstance(s, XsPolygon):
            arr = s.polygon.exterior.array
            us += [float(np.min(arr[:, 0])), float(np.max(arr[:, 0]))]
            zs += [float(np.min(arr[:, 1])), float(np.max(arr[:, 1]))]
        else:
            zs += [s.z_bottom, s.z_bottom + s.thickness]
            us += [v for v in (s.u_min, s.u_max) if v is not None]
    for d in xs.dielectrics:
        r = d.region
        if isinstance(r, XsSlab):
            zs += [r.z_bottom, r.z_top]
            us += [v for v in (r.u_min, r.u_max) if v is not None]
        else:
            arr = r.polygon.exterior.array
            us += [float(np.min(arr[:, 0])), float(np.max(arr[:, 0]))]
            zs += [float(np.min(arr[:, 1])), float(np.max(arr[:, 1]))]
    h_span = max(zs) - min(zs)
    w_span = max(us) - min(us) if us else 0.0
    h_char = max(h_span, w_span, 1e-9)
    h_trunc = h_char
    if params.window:
        # The field of the signals falls off on the scale of the stack height and of the signal
        # cluster width when a reference plane spans the cluster; a wide window of coplanar
        # copper then does not move the open boundary. Without a covering plane (a weakly
        # referenced section) the field reaches the whole window, so the full length is kept.
        sig_u = [v for c in xs.conductors if c.role == "signal" for v in _u_extent(c.shape)]
        if sig_u:
            lo, hi = min(sig_u), max(sig_u)
            reach = max(h_span, hi - lo)
            if _plane_covers(xs, lo - reach, hi + reach):
                h_trunc = max(h_span, hi - lo, 1e-9)
    u_trunc = (max(abs(v) for v in us) if us else 0.0) + params.truncation * h_trunc
    z_lo, z_hi = min(zs), max(zs)

    sig_z = [
        (c.shape.z_bottom + c.shape.thickness / 2)
        if isinstance(c.shape, XsTrapezoid)
        else (c.shape.z_center if isinstance(c.shape, XsCircle) else None)
        for c in xs.conductors
        if c.role == "signal"
    ]
    sig_zc = (
        float(np.mean([z for z in sig_z if z is not None]))
        if any(z is not None for z in sig_z)
        else 0.5 * (z_lo + z_hi)
    )
    iface_z: list[float] = []
    for d in xs.dielectrics:
        if isinstance(d.region, XsSlab):
            iface_z += [d.region.z_bottom, d.region.z_top]

    t_thick: list[float] = []
    for c in xs.conductors:
        s = c.shape
        s_id = c.id
        poly: Polygon | None = None
        rib: LineString | None = None
        area = 0.0
        if isinstance(s, XsTrapezoid):
            wm = 0.5 * (s.w_bottom + s.w_top)
            if s.thickness < _THIN_REL * min(s.w_bottom, s.w_top, h_char):
                zr = s.z_bottom + s.thickness / 2
                for zi in iface_z:  # snap onto a dielectric interface the strip touches
                    if s.z_bottom - 2 * s.thickness <= zi <= s.z_bottom + 3 * s.thickness:
                        zr = zi
                        break
                rib = LineString([(s.u_center - wm / 2, zr), (s.u_center + wm / 2, zr)])
                notes.append(f"{c.id}: t={s.thickness:.3g} m collapsed to a zero-thickness ribbon")
            else:
                hb, ht = s.w_bottom / 2, s.w_top / 2
                poly = Polygon(
                    [
                        (s.u_center - hb, s.z_bottom),
                        (s.u_center + hb, s.z_bottom),
                        (s.u_center + ht, s.z_bottom + s.thickness),
                        (s.u_center - ht, s.z_bottom + s.thickness),
                    ]
                )
                t_thick.append(s.thickness)
            area = wm * s.thickness
        elif isinstance(s, XsCircle):
            poly = _circle_poly(s.u_center, s.z_center, s.radius)
            area = math.pi * s.radius**2
        elif isinstance(s, XsPolygon):
            poly = Polygon(
                _ring_pts(s.polygon.exterior.array), [_ring_pts(h.array) for h in s.polygon.holes]
            )
            area = float(poly.area)
        else:
            u0 = -u_trunc if s.u_min is None else s.u_min
            u1 = u_trunc if s.u_max is None else s.u_max
            z_top = s.z_bottom + s.thickness
            if s.thickness <= 0.0:
                rib = LineString([(u0, s.z_bottom), (u1, s.z_bottom)])
            elif (s.u_min is None and s.u_max is None) or s.thickness < _THIN_REL * min(
                u1 - u0, h_char
            ):
                zr = z_top if sig_zc >= z_top else s.z_bottom
                for zi in iface_z:  # snap onto a dielectric interface the plane touches
                    if s.z_bottom - 2 * s.thickness <= zi <= z_top + 2 * s.thickness:
                        zr = zi
                        break
                rib = LineString([(u0, zr), (u1, zr)])
                notes.append(
                    f"{s_id}: plane thickness collapsed; modelled by its signal-facing surface"
                )
            else:
                poly = Polygon([(u0, s.z_bottom), (u1, s.z_bottom), (u1, z_top), (u0, z_top)])
                t_thick.append(s.thickness)
            area = (u1 - u0) * s.thickness
        cond_polys.append(poly)
        cond_ribbons.append(rib)
        ids.append(c.id)
        roles.append(c.role)
        sigmas.append(c.conductivity)
        areas.append(area)
        roughs.append((c.roughness_top, c.roughness_bottom, c.roughness_side))

    # ---- dielectric zones
    zone_models: list[object | None] = [None]
    zone_polys: list[Polygon] = []
    zone_dkz: list[float] = [1.0]
    order = sorted(range(len(xs.dielectrics)), key=lambda i: (-xs.dielectrics[i].priority, i))
    for i in order:
        d = xs.dielectrics[i]
        r = d.region
        if isinstance(r, XsSlab):
            u0 = -u_trunc if r.u_min is None else r.u_min
            u1 = u_trunc if r.u_max is None else r.u_max
            zp = Polygon([(u0, r.z_bottom), (u1, r.z_bottom), (u1, r.z_top), (u0, r.z_top)])
        else:
            assert isinstance(r, XsRegion)
            zp = Polygon(
                _ring_pts(r.polygon.exterior.array), [_ring_pts(h.array) for h in r.polygon.holes]
            )
        zone_models.append(d.model)
        zone_polys.append(zp)
        zone_dkz.append(d.dk_z_over_dk_xy)
        if d.dk_z_over_dk_xy != 1.0:
            notes.append("anisotropic dk_z/dk_xy ignored by the BEM (dk_xy used)")
    zone_keys = [_zone_key(None, 1.0, xs.background_dk)] + [
        _zone_key(m, zone_dkz[k + 1], xs.background_dk) for k, m in enumerate(zone_models[1:])
    ]

    def zone_at(pts: F64) -> I64:
        out = np.zeros(len(pts), dtype=np.int64)
        for k in range(len(zone_polys) - 1, -1, -1):  # last = lowest priority first, so high wins
            m = shapely.contains_xy(zone_polys[k], pts[:, 0], pts[:, 1])
            out[m] = k + 1
        return out

    # ---- noding
    t_min = min(t_thick) if t_thick else h_char
    grid = max(1e-13, 1e-6 * min(t_min, h_char))
    lines: list[shapely.Geometry] = []
    for zp in zone_polys:
        lines.append(zp.boundary)
    for cp in cond_polys:
        if cp is not None:
            lines.append(cp.boundary)
    for rb in cond_ribbons:
        if rb is not None:
            lines.append(rb)
    lines = [shapely.set_precision(g, grid) for g in lines]
    noded = shapely.line_merge(unary_union(lines))  # degree-2 joins only
    pieces: list[LineString] = []
    geoms = noded.geoms if isinstance(noded, MultiLineString) else [noded]
    for g in geoms:
        if isinstance(g, LineString) and g.length > 0:
            pieces.append(g)

    cpoly_idx = [i for i, p in enumerate(cond_polys) if p is not None]
    crib_idx = [i for i, r in enumerate(cond_ribbons) if r is not None]
    tol = max(2 * grid, 1e-4 * min(t_min, h_char))

    # the probe points beside each noded piece must stay inside the thinnest layer they sit on
    z_levels = np.unique(
        np.round(
            np.array(
                [z for c in xs.conductors for z in _z_bounds(c.shape)]
                + [z for d in xs.dielectrics for z in _z_bounds(d.region)]
            ),
            12,
        )
    )
    z_gaps = np.diff(z_levels)
    z_gaps = z_gaps[z_gaps > 1e-11]
    probe_max = min(0.25 * h_char, 0.1 * float(z_gaps.min())) if len(z_gaps) else 0.25 * h_char

    recs: list[tuple[F64, int, int, int, int]] = []  # (coords, cond, kind, zone1, zone2)
    for pc in pieces:
        coords = np.asarray(pc.coords, dtype=float)
        if len(coords) < 2:
            continue
        seg = np.diff(coords, axis=0)
        sl = np.hypot(seg[:, 0], seg[:, 1])
        tvec = seg / np.maximum(sl, 1e-300)[:, None]
        nvec = np.stack([-tvec[:, 1], tvec[:, 0]], axis=1)
        mids = 0.5 * (coords[:-1] + coords[1:])
        delta = np.minimum(np.maximum(1e-3 * sl, 2 * grid), max(probe_max, 2 * grid))[:, None]
        pl, pr = mids + delta * nvec, mids - delta * nvec
        mpts = shapely.points(mids)
        nseg = len(sl)
        dropped = np.zeros(nseg, dtype=bool)
        owner = np.full(nseg, -1)
        for ci in cpoly_idx:
            cp = cond_polys[ci]
            assert cp is not None
            on_edge = shapely.distance(cp.boundary, mpts) <= tol
            inside = shapely.contains_xy(cp, mids[:, 0], mids[:, 1]) & ~on_edge
            dropped |= inside & (owner < 0)
            owner = np.where((owner < 0) & on_edge & ~dropped, ci, owner)
        rib_owner = np.full(nseg, -1)
        for ci in crib_idx:
            rb = cond_ribbons[ci]
            assert rb is not None
            near = shapely.distance(rb, mpts) <= tol
            rib_owner = np.where((rib_owner < 0) & near, ci, rib_owner)
        zone_l = zone_at(pl)
        zone_r = zone_at(pr)
        flip = np.zeros(nseg, dtype=bool)
        for ci in cpoly_idx:
            sel = owner == ci
            if sel.any():
                cp = cond_polys[ci]
                assert cp is not None
                flip[sel] = shapely.contains_xy(cp, pl[sel, 0], pl[sel, 1])
        # class tuple per segment
        cls: list[tuple[int, int, int, int, int] | None] = []
        for k in range(nseg):
            if dropped[k]:
                cls.append(None)
            elif owner[k] >= 0:
                zo = int(zone_r[k] if flip[k] else zone_l[k])
                cls.append((KIND_THICK, int(owner[k]), int(flip[k]), zo, -1))
            elif rib_owner[k] >= 0:
                cls.append((KIND_RIBBON, int(rib_owner[k]), 0, int(zone_l[k]), int(zone_r[k])))
            elif zone_keys[int(zone_l[k])] == zone_keys[int(zone_r[k])]:
                cls.append(None)
            else:
                cls.append((KIND_INTERFACE, -1, 0, int(zone_l[k]), int(zone_r[k])))
        k0 = 0
        while k0 < nseg:
            k1 = k0
            while k1 + 1 < nseg and cls[k1 + 1] == cls[k0]:
                k1 += 1
            cl = cls[k0]
            if cl is not None:
                run = coords[k0 : k1 + 2]
                if cl[0] == KIND_THICK and cl[2]:
                    run = run[::-1]
                recs.append((run, cl[1], cl[0], cl[3], cl[4]))
            k0 = k1 + 1

    # ---- discretization
    # smallest feature: signal widths and distances between conductors (not just the overall size)
    geoms_c = [
        p_ if p_ is not None else r_ for p_, r_ in zip(cond_polys, cond_ribbons, strict=True)
    ]
    f_min = h_char
    for i, ga in enumerate(geoms_c):
        assert ga is not None
        if roles[i] == "signal":
            bx = ga.bounds
            f_min = min(f_min, max(bx[2] - bx[0], 1e-12))
        for gb in geoms_c[i + 1 :]:
            assert gb is not None
            d_ = float(ga.distance(gb))
            if d_ > 1e-12:
                f_min = min(f_min, d_)
    s_min = min(params.s_min_rel * f_min, params.min_feature_ratio * t_min)
    s_max = params.s_max_rel * h_char
    # split pieces at sharp vertices, collecting sub-polylines and singular points
    subs: list[tuple[F64, int, int, int, int, int]] = []  # + piece id
    sing: list[F64] = []
    for pid, (coords, ci, kd, z1, z2) in enumerate(recs):
        cuts = [0]
        if len(coords) > 2:
            v = np.diff(coords, axis=0)
            ang = np.arctan2(v[:, 1], v[:, 0])
            turn = np.abs((np.diff(ang) + np.pi) % (2 * np.pi) - np.pi)
            cuts += [int(i) + 1 for i in np.nonzero(turn > _SHARP)[0]]
        cuts.append(len(coords) - 1)
        closed = bool(np.allclose(coords[0], coords[-1]))
        for c0, c1 in pairwise(cuts):
            sp = coords[c0 : c1 + 1]
            if len(sp) >= 2:
                subs.append((sp, ci, kd, z1, z2, pid))
        for cut in cuts[1:-1]:
            sing.append(coords[cut])
        if not closed or len(cuts) > 2:
            sing.append(coords[0])
            sing.append(coords[-1])
    sing_arr = np.asarray(sing) if sing else np.zeros((0, 2))

    # segment soup of all pieces for the proximity (gap) estimate
    seg_a, seg_b, seg_pid, seg_grp, seg_curved = [], [], [], [], []
    for sp, _ci, _kd, _z1, _z2, pid in subs:
        seg_a.append(sp[:-1])
        seg_b.append(sp[1:])
        seg_pid.append(np.full(len(sp) - 1, pid))
        seg_grp.append(np.full(len(sp) - 1, _group(_ci, _kd, pid)))
        seg_curved.append(np.full(len(sp) - 1, math.isfinite(_curvature_cap(sp))))
    SA, SB, SP = np.concatenate(seg_a), np.concatenate(seg_b), np.concatenate(seg_pid)
    SG = np.concatenate(seg_grp)
    SCV = np.concatenate(seg_curved)
    SD = SB - SA
    SL2 = np.maximum((SD**2).sum(axis=1), 1e-300)

    def gap_distance(pts: F64, own_pid: int, own_grp: int, own_curved: bool) -> tuple[F64, F64]:
        """Distance to pieces of other conductors/interfaces, and to other pieces of this one."""
        rel = pts[:, None, :] - SA[None, :, :]
        tt = np.clip((rel * SD[None]).sum(axis=2) / SL2[None], 0.0, 1.0)
        res = rel - tt[..., None] * SD[None]
        dist = np.hypot(res[..., 0], res[..., 1])
        # only other conductors set the charge scale, and only where a curved boundary is
        # involved (flat parallel faces have a smooth, slowly varying charge)
        other = (own_grp != SG) & (SG >= 10_000_000) & (SCV | own_curved)
        same = (own_grp == SG) & (own_pid != SP)
        d_o = np.min(dist[:, other], axis=1) if np.any(other) else np.full(len(pts), np.inf)
        d_s = np.min(dist[:, same], axis=1) if np.any(same) else np.full(len(pts), np.inf)
        return np.asarray(d_o), np.asarray(d_s)

    plane_idx = {i for i, c in enumerate(xs.conductors) if isinstance(c.shape, XsPlane)}
    sig_geoms = [g for g, r_ in zip(geoms_c, roles, strict=True) if r_ == "signal"]
    focus = unary_union(sig_geoms) if sig_geoms and params.far_ratio > 0 else None
    a_list, b_list, c_list, k_list, z1_list, z2_list, f_list = [], [], [], [], [], [], []
    n_samp = 33
    for sp, ci, kd, z1, z2, pid in subs:
        seglen = np.hypot(*np.diff(sp, axis=0).T)
        cum = np.concatenate([[0.0], np.cumsum(seglen)])
        total = float(cum[-1])
        if total <= 0:
            continue
        # samples: cosine-clustered ends plus geometric clusters at the projection of nearby
        # singular points (a strip corner above a long plane refines the plane under it)
        ss = 0.5 * (1.0 - np.cos(np.linspace(0.0, math.pi, n_samp)))
        ls = ss * total
        v_pos, v_cap = _curvature_caps(sp)
        if len(v_pos):
            ls = np.unique(np.concatenate([ls, v_pos]))
        if len(sing_arr):
            near = sing_arr
            chord = sp[-1] - sp[0]
            clen = float(np.hypot(*chord))
            if clen > 0:
                dirv = chord / clen
                lp = (near - sp[0]) @ dirv
                perp = np.abs((near - sp[0]) @ np.array([-dirv[1], dirv[0]]))
                lp = lp[(perp < 2.0 * h_char) & (lp > 0) & (lp < total)]
                offs = s_min * 2.0 ** np.arange(0, 40, 1.0)
                offs = offs[offs < total]
                extra = [lp]
                for off in offs:
                    extra += [lp + off, lp - off]
                ls = np.unique(np.clip(np.concatenate([ls, *extra]), 0.0, total))
        pts = np.stack([np.interp(ls, cum, sp[:, 0]), np.interp(ls, cum, sp[:, 1])], axis=1)
        if len(sing_arr):
            d_sing = np.hypot(
                pts[:, None, 0] - sing_arr[None, :, 0], pts[:, None, 1] - sing_arr[None, :, 1]
            ).min(axis=1)
        else:
            d_sing = np.full(len(pts), np.inf)
        d_gap, d_same = gap_distance(
            pts, pid, _group(ci, kd, pid), math.isfinite(_curvature_cap(sp))
        )
        raw = np.minimum(params.alpha * d_sing, params.gap_ratio * params.alpha * d_gap)
        if focus is not None:
            # far field: copper far from the signals carries little of their charge, so its
            # corners are not resolved below far_ratio * distance (graded, not uniform)
            dist_f = np.maximum(
                shapely.distance(focus, shapely.points(pts)) - params.far_start * h_trunc, 0.0
            )
            raw = np.maximum(raw, params.far_ratio * dist_f)
        cap_same = 3.0 * d_same
        if ci >= 0 and ci in plane_idx and params.plane_relax > 0:
            # a thin plane has two faces a few tens of um apart; capping every panel at 3x that
            # makes uniform, needlessly fine panels over its whole length. The faces of a plane
            # carry smooth, equal densities away from its edges, so the cap grows with the
            # distance to the nearest edge / singular point.
            cap_same = np.maximum(cap_same, params.plane_relax * d_sing)
        psz = np.clip(np.minimum(raw, cap_same), s_min, s_max)
        if len(v_pos):
            # curvature limit near each curved vertex, relaxing away from it (not one global cap
            # set by the tightest fillet of a long piece)
            cap_v = np.abs(ls[:, None] - v_pos[None, :]) * params.alpha + v_cap[None, :]
            psz = np.minimum(psz, cap_v.min(axis=1))
        # integral of dl/size, size varying linearly between samples (exact for s = alpha * d)
        s0, s1, dl = psz[:-1], psz[1:], np.diff(ls)
        ds = s1 - s0
        flat = np.abs(ds) < 1e-9 * s0
        seg = np.where(flat, dl / s0, dl * np.log(s1 / s0) / np.where(flat, 1.0, ds))
        cumi = np.concatenate([[0.0], np.cumsum(seg)])
        n_pan = max(1, math.ceil(cumi[-1]))
        targets = np.linspace(0.0, cumi[-1], n_pan + 1)
        # invert the integral exactly inside each interval (size is linear there), so geometric
        # grading survives between widely spaced samples (np.interp would make the panels uniform)
        k_int = np.clip(np.searchsorted(cumi, targets, side="right") - 1, 0, len(dl) - 1)
        c_loc = targets - cumi[k_int]
        slope = np.where(dl[k_int] > 0, ds[k_int] / np.where(dl[k_int] > 0, dl[k_int], 1.0), 0.0)
        s_lo = s0[k_int]
        flat_k = np.abs(slope) < 1e-12
        with np.errstate(over="ignore", invalid="ignore"):
            off = np.where(
                flat_k,
                c_loc * s_lo,
                s_lo * np.expm1(slope * c_loc) / np.where(flat_k, 1.0, slope),
            )
        pos = np.clip(ls[k_int] + off, ls[k_int], ls[k_int] + dl[k_int])
        pos[0], pos[-1] = ls[0], ls[-1]
        nx_ = np.interp(pos, cum, sp[:, 0])
        ny_ = np.interp(pos, cum, sp[:, 1])
        nodes = np.stack([nx_, ny_], axis=1)
        a_list.append(nodes[:-1])
        b_list.append(nodes[1:])
        m = n_pan
        c_list.append(np.full(m, ci))
        k_list.append(np.full(m, kd))
        z1_list.append(np.full(m, z1))
        z2_list.append(np.full(m, z2))
        # face for roughness, from the left-normal direction of each panel
        d = nodes[1:] - nodes[:-1]
        nzv = d[:, 0] / np.hypot(d[:, 0], d[:, 1])  # normal z = tangent u component
        f_list.append(np.where(nzv > 0.7, FACE_TOP, np.where(nzv < -0.7, FACE_BOTTOM, FACE_SIDE)))

    a = np.concatenate(a_list)
    b = np.concatenate(b_list)
    keep = np.hypot(*(b - a).T) > 1e-15
    cond_arr = np.concatenate(c_list)
    # interface panels carry no conductor
    panels = Panels(
        a=a[keep],
        b=b[keep],
        cond=cond_arr[keep],
        kind=np.concatenate(k_list)[keep],
        zone1=np.concatenate(z1_list)[keep],
        zone2=np.concatenate(z2_list)[keep],
        face=np.concatenate(f_list)[keep],
    )
    notes.append(
        f"open boundaries truncated at {params.truncation:g}*{h_char * 1e3:.4g} mm "
        f"(|u| <= {u_trunc * 1e3:.4g} mm)"
    )
    return Problem(
        panels=panels,
        zone_models=zone_models,
        background_dk=xs.background_dk,
        cond_ids=ids,
        cond_roles=[str(r_) for r_ in roles],
        cond_sigma=sigmas,
        cond_area=areas,
        cond_rough=roughs,
        notes=notes,
        h_char=h_char,
        u_trunc=u_trunc,
    )
