# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cross-section features and the cause of a change between two neighbouring sections."""

from __future__ import annotations

from pydantic import Field

from oa_model._base import OAModel
from oa_model.xsection import CrossSection, XsPlane, XsTrapezoid

__all__ = ["CAUSES", "KeyFeature", "classify", "features_of", "static_causes"]

CAUSES = (
    "layer_change",
    "width_change",
    "neckdown",
    "ref_change",
    "no_reference",
    "plane_edge",
    "coplanar_ground",
    "pair_uncoupled",
    "pair_coupled",
    "pair_gap_change",
    "geometry_change",
)
NM = 1e-9
_COVER_MIN = 0.5  # a plane counts as a reference when it covers half the trace footprint


class KeyFeature(OAModel):
    topology: str = Field(description="CrossSection.topology_hint of the section.")
    layer: str | None = Field(default=None, description="Trace layer id.")
    n_signals: int = Field(description="Signal conductors in the section [count].")
    width: float = Field(description="Bottom width of the first signal conductor [m].")
    gap: float | None = Field(default=None, description="Edge gap of the first two signals [m].")
    refs: tuple[tuple[int, str | None], ...] = Field(
        description="Covering reference planes as (z_bottom [nm], net), sorted."
    )
    truncated: bool = Field(description="A covering plane ends (plane edge) inside the window.")
    coplanar_gap: float | None = Field(
        default=None, description="Gap to the nearest same-layer ground copper [m]."
    )
    h: float | None = Field(default=None, description="Height over the nearest reference [m].")


def _cover(lo: float, hi: float, p: XsPlane) -> float:
    a = lo if p.u_min is None else max(lo, p.u_min)
    b = hi if p.u_max is None else min(hi, p.u_max)
    return max(0.0, b - a) / max(hi - lo, 1e-15)


def features_of(xs: CrossSection) -> KeyFeature:
    sigs = [
        c.shape for c in xs.conductors if c.role == "signal" and isinstance(c.shape, XsTrapezoid)
    ]
    lo = min(s.u_center - s.w_bottom / 2 for s in sigs)
    hi = max(s.u_center + s.w_bottom / 2 for s in sigs)
    z_sig = sigs[0].z_bottom
    refs: list[tuple[int, str | None]] = []
    truncated = False
    h: float | None = None
    for c in xs.conductors:
        if (
            c.role == "ground"
            and isinstance(c.shape, XsPlane)
            and _cover(lo, hi, c.shape) >= _COVER_MIN
        ):
            refs.append((round(c.shape.z_bottom / NM), c.net))
            if c.shape.u_min is not None or c.shape.u_max is not None:
                truncated = True
            dz = min(
                abs(z_sig - (c.shape.z_bottom + c.shape.thickness)),
                abs(c.shape.z_bottom - (z_sig + sigs[0].thickness)),
            )
            h = dz if h is None else min(h, dz)
    cop: float | None = None
    for c in xs.conductors:
        if c.role == "ground" and isinstance(c.shape, XsTrapezoid):
            g = max(
                c.shape.u_center - c.shape.w_bottom / 2 - hi,
                lo - (c.shape.u_center + c.shape.w_bottom / 2),
            )
            cop = g if cop is None else min(cop, g)
    gap: float | None = None
    if len(sigs) >= 2:
        a, b = sorted(sigs[:2], key=lambda s: s.u_center)
        gap = (b.u_center - b.w_bottom / 2) - (a.u_center + a.w_bottom / 2)
    return KeyFeature(
        topology=xs.topology_hint,
        layer=xs.provenance.layer if xs.provenance else None,
        n_signals=len(sigs),
        width=sigs[0].w_bottom,
        gap=gap,
        refs=tuple(sorted(refs, key=lambda r: (r[0], r[1] or ""))),
        truncated=truncated,
        coplanar_gap=cop,
        h=h,
    )


def _coplanar(f: KeyFeature) -> bool:
    if f.coplanar_gap is None:
        return False
    return f.coplanar_gap < 2.0 * (f.h if f.h is not None else f.width)


def static_causes(f: KeyFeature, pair: bool) -> tuple[str, ...]:
    """Hazards that make a section a discontinuity regardless of its neighbours."""
    out: list[str] = []
    if not f.refs:
        out.append("no_reference")
    elif f.truncated:
        out.append("plane_edge")
    if pair and f.n_signals < 2:
        out.append("pair_uncoupled")
    return tuple(out)


def classify(prev: KeyFeature, cur: KeyFeature, tag: str, pair: bool) -> tuple[str, ...]:
    """Causes of the step from `prev` to `cur`; `tag` is the XsSample event of the first sample."""
    out: list[str] = []
    if tag == "layer_change" or prev.layer != cur.layer:
        out.append("layer_change")
    if abs(prev.width - cur.width) > NM:
        out.append("neckdown" if cur.width < 0.8 * prev.width else "width_change")
    if prev.refs != cur.refs:
        out.append("ref_change" if cur.refs else "no_reference")
    if cur.truncated and not prev.truncated and cur.refs:
        out.append("plane_edge")
    if _coplanar(prev) != _coplanar(cur):
        out.append("coplanar_ground")
    if pair and prev.n_signals != cur.n_signals:
        out.append("pair_coupled" if cur.n_signals > prev.n_signals else "pair_uncoupled")
    elif pair and prev.gap is not None and cur.gap is not None and abs(prev.gap - cur.gap) > NM:
        out.append("pair_gap_change")
    if tag in ("ref_change", "no_reference", "plane_edge") and not any(
        c in out for c in ("ref_change", "no_reference", "plane_edge")
    ):
        out.append(tag)
    if not out and prev != cur:
        out.append("geometry_change")
    return tuple(dict.fromkeys(out))
