# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Map a ``CrossSection`` onto a closed-form model (or report that none applies).

``extract(xs)`` returns a :class:`Topology` only when the section is *exactly* one of the
structures the formulas describe (no extra conductors, no mask layers, ...). Anything else returns
``None`` and the service falls back to the BEM.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from oa_impedance import _physics_compat as phys
from oa_impedance import closed_form as cf
from oa_model.xsection import (
    CrossSection,
    XsCircle,
    XsConductor,
    XsDielectric,
    XsPlane,
    XsSlab,
    XsTrapezoid,
)

F64 = npt.NDArray[np.float64]

__all__ = ["Topology", "evaluate", "extract", "loss_db_per_m"]

_TOL = 2e-9  # 2 nm geometric tolerance for "touching"


@dataclass
class Topology:
    kind: str
    params: dict[str, float]
    model: object | None  # the single dielectric model (None: background)
    signals: tuple[XsConductor, ...]
    notes: list[str] = field(default_factory=list)


def _planes(xs: CrossSection, role: str = "ground") -> list[XsConductor]:
    return [c for c in xs.conductors if c.role == role and isinstance(c.shape, XsPlane)]


def _slabs_infinite(xs: CrossSection) -> list[XsDielectric] | None:
    out = []
    for d in xs.dielectrics:
        if (
            not isinstance(d.region, XsSlab)
            or d.region.u_min is not None
            or d.region.u_max is not None
        ):
            return None
        out.append(d)
    return out


def _same_model(slabs: list[XsDielectric]) -> object | None:
    ms = {repr(d.model.model_dump()) for d in slabs}
    return slabs[0].model if len(ms) == 1 else None


def _covers(slabs: list[XsDielectric], lo: float, hi: float) -> bool:
    iv = sorted((d.region.z_bottom, d.region.z_top) for d in slabs if isinstance(d.region, XsSlab))
    pos = lo
    for a, b in iv:
        if a > pos + _TOL:
            return False
        pos = max(pos, b)
    return pos >= hi - _TOL


def _trap(c: XsConductor) -> XsTrapezoid | None:
    return c.shape if isinstance(c.shape, XsTrapezoid) else None


def extract(xs: CrossSection) -> Topology | None:
    sigs = tuple(c for c in xs.conductors if c.role == "signal")
    grounds = [c for c in xs.conductors if c.role == "ground"]
    if any(c.role == "floating" for c in xs.conductors):
        return None
    slabs = _slabs_infinite(xs)
    if slabs is None:
        return None
    hint = xs.topology_hint
    notes: list[str] = []

    if hint == "wire_over_plane" and len(sigs) == 1 and len(grounds) == 1 and not slabs:
        wire, gp = sigs[0].shape, grounds[0].shape
        if isinstance(wire, XsCircle) and isinstance(gp, XsPlane):
            top = gp.z_bottom + gp.thickness
            return Topology(
                "wire_over_plane",
                {"r": wire.radius, "h": wire.z_center - top, "er": xs.background_dk},
                None,
                sigs,
            )
        return None

    traps = [_trap(c) for c in sigs]
    if not sigs or any(t is None for t in traps):
        return None
    planes = _planes(xs)
    if len(planes) != len(grounds):
        return None
    model = _same_model(slabs) if slabs else None
    if slabs and model is None:
        return None
    t0 = traps[0]
    assert t0 is not None

    def width(t: XsTrapezoid) -> float:
        return 0.5 * (t.w_bottom + t.w_top)

    if len(sigs) == 1 and hint in ("microstrip", "embedded_microstrip") and len(planes) == 1:
        p = planes[0].shape
        assert isinstance(p, XsPlane)
        p_top = p.z_bottom + p.thickness
        h = t0.z_bottom - p_top
        if h <= 0 or not slabs or not _covers(slabs, p_top, t0.z_bottom):
            return None
        z_top_diel = max(d.region.z_top for d in slabs if isinstance(d.region, XsSlab))
        cover = z_top_diel - (t0.z_bottom + t0.thickness)
        if abs(t0.w_top - t0.w_bottom) > _TOL:
            notes.append("trapezoid replaced by its mean width")
        if hint == "microstrip" and z_top_diel > t0.z_bottom + _TOL:
            return None  # dielectric above the plane of the strip: not a surface microstrip
        if hint == "embedded_microstrip" and cover < -_TOL:
            return None
        return Topology(
            hint,
            {"w": width(t0), "h": h, "t": t0.thickness, "cover": max(cover, 0.0)},
            model,
            sigs,
            notes,
        )

    if len(sigs) == 1 and hint in ("stripline", "offset_stripline") and len(planes) == 2:
        pl = sorted(planes, key=lambda c: c.shape.z_bottom)  # type: ignore[union-attr]
        lo, hi = pl[0].shape, pl[1].shape
        assert isinstance(lo, XsPlane) and isinstance(hi, XsPlane)
        z_lo, z_hi = lo.z_bottom + lo.thickness, hi.z_bottom
        if not slabs or not _covers(slabs, z_lo, z_hi):
            return None
        b = z_hi - z_lo
        h1 = t0.z_bottom - z_lo
        h2 = z_hi - (t0.z_bottom + t0.thickness)
        if h1 <= 0 or h2 <= 0:
            return None
        if abs(t0.w_top - t0.w_bottom) > _TOL:
            notes.append("trapezoid replaced by its mean width")
        kind = "stripline" if abs(h1 - h2) < 1e-3 * b else "offset_stripline"
        return Topology(
            kind,
            {"w": width(t0), "b": b, "t": t0.thickness, "h1": h1, "h2": h2},
            model,
            sigs,
            notes,
        )

    if len(sigs) == 2 and hint in ("edge_coupled_microstrip", "edge_coupled_stripline"):
        t1 = traps[1]
        assert t1 is not None
        if (
            abs(t1.z_bottom - t0.z_bottom) > _TOL
            or abs(width(t1) - width(t0)) > _TOL
            or abs(t1.thickness - t0.thickness) > _TOL
        ):
            return None
        s = abs(t1.u_center - t0.u_center) - width(t0)
        if s <= 0:
            return None
        if hint == "edge_coupled_microstrip" and len(planes) == 1:
            p = planes[0].shape
            assert isinstance(p, XsPlane)
            p_top = p.z_bottom + p.thickness
            if not slabs or not _covers(slabs, p_top, t0.z_bottom):
                return None
            if (
                max(d.region.z_top for d in slabs if isinstance(d.region, XsSlab))
                > t0.z_bottom + _TOL
            ):
                return None
            return Topology(
                hint,
                {"w": width(t0), "s": s, "h": t0.z_bottom - p_top, "t": t0.thickness},
                model,
                sigs,
                notes,
            )
        if hint == "edge_coupled_stripline" and len(planes) == 2:
            pl = sorted(planes, key=lambda c: c.shape.z_bottom)  # type: ignore[union-attr]
            lo, hi = pl[0].shape, pl[1].shape
            assert isinstance(lo, XsPlane) and isinstance(hi, XsPlane)
            z_lo, z_hi = lo.z_bottom + lo.thickness, hi.z_bottom
            if not slabs or not _covers(slabs, z_lo, z_hi):
                return None
            h1 = t0.z_bottom - z_lo
            h2 = z_hi - (t0.z_bottom + t0.thickness)
            if abs(h1 - h2) > 1e-3 * (z_hi - z_lo):
                notes.append("offset coupled stripline treated as centred (approximate)")
            return Topology(
                hint,
                {"w": width(t0), "s": s, "b": z_hi - z_lo, "t": t0.thickness},
                model,
                sigs,
                notes,
            )
        return None

    if len(sigs) == 1 and hint in ("cpw", "cpwg"):
        side = [p for p in planes if p.shape.u_min is None or p.shape.u_max is None]  # type: ignore[union-attr]
        back = [p for p in planes if p not in side]
        if len(side) != 2 or (hint == "cpw" and back) or (hint == "cpwg" and len(back) != 1):
            return None
        left = [p for p in side if p.shape.u_max is not None and p.shape.u_min is None]  # type: ignore[union-attr]
        right = [p for p in side if p.shape.u_min is not None and p.shape.u_max is None]  # type: ignore[union-attr]
        if len(left) != 1 or len(right) != 1:
            return None
        lp, rp = left[0].shape, right[0].shape
        assert isinstance(lp, XsPlane) and isinstance(rp, XsPlane)
        assert lp.u_max is not None and rp.u_min is not None
        gl = t0.u_center - width(t0) / 2 - lp.u_max
        gr = rp.u_min - (t0.u_center + width(t0) / 2)
        if gl <= 0 or gr <= 0 or abs(gl - gr) > 1e-3 * (gl + gr):
            return None
        params = {"s": width(t0), "g": 0.5 * (gl + gr), "t": t0.thickness}
        h_sub: float | None = None
        if hint == "cpwg":
            bp = back[0].shape
            assert isinstance(bp, XsPlane)
            h_sub = t0.z_bottom - (bp.z_bottom + bp.thickness)
            if h_sub <= 0:
                return None
            params["h"] = h_sub
        elif slabs:
            sl = [d for d in slabs if isinstance(d.region, XsSlab)]
            top = max(d.region.z_top for d in sl)  # type: ignore[union-attr]
            bottom = min(d.region.z_bottom for d in sl)  # type: ignore[union-attr]
            if abs(top - t0.z_bottom) > 1e-3 * (top - bottom):
                return None
            h_sub = top - bottom
            params["h"] = h_sub
        if slabs:
            if not _covers(slabs, t0.z_bottom - h_sub if h_sub else t0.z_bottom, t0.z_bottom):
                return None
        else:
            if hint == "cpwg":
                return None
        return Topology(hint, params, model, sigs, notes)
    return None


def _er(topo: Topology, bg: float, f: float) -> tuple[float, float]:
    """(Re er, tan delta) at ``f`` for the topology's dielectric."""
    if topo.model is None:
        return bg, 0.0
    e = phys.eps_complex(topo.model, f)
    return e.real, (-e.imag / e.real if e.real else 0.0)


def evaluate(topo: Topology, xs: CrossSection, f: float) -> cf.CFResult:
    er, _ = _er(topo, xs.background_dk, f)
    p = topo.params
    k = topo.kind
    if k == "microstrip":
        return cf.microstrip_hj(p["w"], p["h"], p["t"], er, f)
    if k == "embedded_microstrip":
        r = cf.embedded_microstrip(p["w"], p["h"], p["t"], er, p["cover"])
        return r
    if k == "stripline":
        return cf.stripline(p["w"], p["b"], p["t"], er)
    if k == "offset_stripline":
        return cf.offset_stripline(p["w"], p["h1"], p["h2"], p["t"], er)
    if k == "edge_coupled_microstrip":
        return cf.edge_coupled_microstrip(p["w"], p["s"], p["h"], p["t"], er)
    if k == "edge_coupled_stripline":
        return cf.edge_coupled_stripline(p["w"], p["s"], p["b"], p["t"], er)
    if k in ("cpw", "cpwg"):
        return cf.cpw(p["s"], p["g"], p["t"], er, p.get("h"), backside=(k == "cpwg"))
    if k == "wire_over_plane":
        z = phys.ETA0 / (2 * math.pi * math.sqrt(er)) * math.acosh((p["h"] + 0.0) / p["r"])
        return cf.CFResult(
            model="wire_over_plane_exact",
            z0=z,
            eeff=er,
            checks=(cf.RangeCheck("h/r", p["h"] / p["r"], 1.0, 1e6),),
            notes=("exact (method of images)",),
        )
    raise ValueError(k)


def loss_db_per_m(topo: Topology, xs: CrossSection, f: float, res: cf.CFResult) -> float:
    """Conductor (Wheeler rule) + dielectric loss [dB/m]; 0 for topologies without a loss model."""
    if f <= 0.0 or res.z0 is None or res.eeff is None:
        return 0.0
    p = topo.params
    sig = topo.signals[0]
    sigma = sig.conductivity
    kr = 0.5 * (
        phys.roughness_factor(sig.roughness_top, f, sigma)
        + phys.roughness_factor(sig.roughness_bottom, f, sigma)
    )
    er, tand = _er(topo, xs.background_dk, f)
    alpha_c = 0.0
    if topo.kind == "microstrip":

        def z_air(w: float, h: float, t: float) -> float:
            r = cf.microstrip_hj(w, h, t, 1.0)
            assert r.z0 is not None
            return r.z0

        alpha_c, _ = cf.wheeler_conductor_loss_np_per_m(
            z_air, p["w"], p["h"], p["t"], res.z0, f, sigma, kr
        )
    elif topo.kind == "stripline":

        def z_air_s(w: float, b: float, t: float) -> float:
            return cf.wheeler_z0(w, b, t, 1.0)

        # recess: width -delta, spacing +delta (planes), thickness -delta
        alpha_c, _ = cf.wheeler_conductor_loss_np_per_m(
            z_air_s, p["w"], p["b"], p["t"], res.z0, f, sigma, kr
        )
    alpha_d = cf.dielectric_loss_np_per_m(f, er, res.eeff, tand)
    return float((alpha_c + alpha_d) * 20.0 / math.log(10.0))
