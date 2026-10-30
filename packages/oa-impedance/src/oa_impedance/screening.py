# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Error bounds of the static closed-form impedances against the BEM (WP-516).

A closed-form result is a *screening* value (``accuracy_class == "screening"``). For a decision
("is Z inside target +- tolerance?") the screening value is only usable together with a bound on
its distance from the field-solved value. :func:`screening_bound` returns that bound, as a
relative error ``b`` of the closed form against the BEM::

    |Z_cf / Z_bem - 1| <= b          =>   Z_bem in [Z_cf / (1 + b), Z_cf / (1 - b)]

The bounds are *calibrated against the BEM*, not taken from the formulas' papers: the papers
quote their fit error against the exact static solution (HJ: 0.01 to 0.3 %), which does not cover
what the decision actually compares with (finite copper thickness, mean-width trapezoids, a plane
that stops near the trace). Each regime below lists the calibration box and the largest error
observed in it on a grid (``tests/test_screening.py`` re-runs the grid and asserts the table);
the stored bound is at least 2x the largest observed error. Outside the box, or when a plane
edge is nearer than ``8 h``, there is no bound and the caller must use the BEM.

Only static (``f = 0``) results carry a bound: the Kirschning-Jansen dispersion is not part of
the calibration.
"""

from __future__ import annotations

from dataclasses import dataclass

from oa_impedance._dielectric import eps_at
from oa_impedance.topology import Topology
from oa_model.xsection import CrossSection, XsPlane, XsTrapezoid

__all__ = ["BOUND_FLAG", "MIN_PLANE_MARGIN", "REGIMES", "Regime", "screening_bound"]

BOUND_FLAG = "z_rel_bound"  # ValidityFlag code carrying the bound (message starts with the number)
MIN_PLANE_MARGIN = 8.0  # plane edge to trace edge, in units of the dielectric height h (or b)
_MAX_SKEW = 0.02  # |w_top - w_bottom| / w of the traces (calibrated with the mean width)
_ER = (1.5, 12.0)  # relative permittivity range of the calibration


@dataclass(frozen=True)
class Regime:
    """Calibration box of one closed-form family: parameter -> (lo, hi), the bound, and the
    largest error observed on the calibration grid (``observed``, a fraction)."""

    name: str
    box: dict[str, tuple[float, float]]
    bound: float
    observed: float
    bound_extrapolated: float | None = None  # bound when the formula itself is `extrapolated`
    observed_extrapolated: float | None = None


# Parameters: w trace width, h height over the plane, b plane spacing, t thickness, s gap,
# h1/h2 distances to the planes (offset stripline), cover = dielectric above the trace.
REGIMES: dict[str, Regime] = {
    "microstrip": Regime(
        "Hammerstad-Jensen microstrip",
        {"w/h": (0.2, 6.0), "t/h": (0.0, 0.4)},
        bound=0.02,
        observed=0.0095,
    ),
    "embedded_microstrip": Regime(
        "embedded microstrip (cover correction)",
        {"w/h": (0.5, 4.0), "t/h": (0.0, 0.1), "cover/h": (0.2, 1.0)},
        bound=0.10,
        observed=0.0501,
    ),
    "stripline": Regime(
        "Cohn + Wheeler stripline",
        {"w/b": (0.1, 1.5), "t/b": (0.0, 0.3)},
        bound=0.02,
        observed=0.0088,
        bound_extrapolated=0.02,
        observed_extrapolated=0.0088,
    ),
    "offset_stripline": Regime(
        "offset stripline (parallel approximation)",
        {"w/b": (0.1, 0.7), "min(h1,h2)/b": (0.2, 0.5), "t/b": (0.0, 0.1)},
        bound=0.015,
        observed=0.0050,
        bound_extrapolated=0.11,
        observed_extrapolated=0.0551,
    ),
    "edge_coupled_microstrip": Regime(
        "Kirschning-Jansen coupled microstrip (Zdiff)",
        {"w/h": (0.5, 2.0), "s/h": (0.2, 4.0), "t/h": (0.0, 0.01)},
        bound=0.045,
        observed=0.0205,
    ),
    "edge_coupled_stripline": Regime(
        "Cohn coupled stripline (Zdiff)",
        {"w/b": (0.1, 0.4), "s/b": (0.1, 0.8), "t/b": (0.0, 0.01)},
        bound=0.015,
        observed=0.0066,
    ),
}


def _inside(box: dict[str, tuple[float, float]], vals: dict[str, float]) -> bool:
    return all(lo <= vals[k] <= hi for k, (lo, hi) in box.items())


def _trace_extent(sigs: tuple[object, ...]) -> tuple[float, float] | None:
    lo, hi = float("inf"), float("-inf")
    for c in sigs:
        shape = getattr(c, "shape", None)
        if not isinstance(shape, XsTrapezoid):
            return None
        w = max(shape.w_bottom, shape.w_top)
        if abs(shape.w_top - shape.w_bottom) > _MAX_SKEW * w:
            return None
        lo, hi = min(lo, shape.u_center - w / 2), max(hi, shape.u_center + w / 2)
    return lo, hi


def _planes_clear(xs: CrossSection, extent: tuple[float, float], scale: float) -> bool:
    """Every ground plane reaches ``MIN_PLANE_MARGIN * scale`` beyond both trace edges."""
    need = MIN_PLANE_MARGIN * scale
    for c in xs.conductors:
        if c.role != "ground":
            continue
        p = c.shape
        if not isinstance(p, XsPlane):
            return False
        if p.u_min is not None and extent[0] - p.u_min < need:
            return False
        if p.u_max is not None and p.u_max - extent[1] < need:
            return False
    return True


def screening_bound(
    topo: Topology, xs: CrossSection, *, extrapolated: bool = False
) -> tuple[float | None, str]:
    """``(b, why)``: the relative bound of the static closed form of ``topo`` against the BEM,
    or ``(None, reason)`` when ``xs`` is outside every calibrated regime."""
    reg = REGIMES.get(topo.kind)
    if reg is None:
        return None, f"no calibrated regime for {topo.kind}"
    p = topo.params
    extent = _trace_extent(topo.signals)
    if extent is None:
        return None, "trace is not a (near-)rectangular trapezoid"
    er = xs.background_dk if topo.model is None else float(eps_at(topo.model, 0.0).real)
    if not _ER[0] <= er <= _ER[1]:
        return None, f"relative permittivity {er:.3g} outside the calibrated {_ER}"
    kind = topo.kind
    if kind in ("microstrip", "embedded_microstrip", "edge_coupled_microstrip"):
        if abs(xs.background_dk - 1.0) > 1e-9:
            return None, "the medium above the trace is not air"
        h = p["h"]
        vals = {"w/h": p["w"] / h, "t/h": p["t"] / h}
        if kind == "embedded_microstrip":
            vals["cover/h"] = p["cover"] / h
        if kind == "edge_coupled_microstrip":
            vals["s/h"] = p["s"] / h
        scale = h
    elif kind == "stripline":
        b = p["b"]
        vals = {"w/b": p["w"] / b, "t/b": p["t"] / b}
        scale = b
    elif kind == "offset_stripline":
        b = p["b"]
        vals = {
            "w/b": p["w"] / b,
            "min(h1,h2)/b": min(p["h1"], p["h2"]) / b,
            "t/b": p["t"] / b,
        }
        scale = b
    else:  # edge_coupled_stripline
        b = p["b"]
        vals = {"w/b": p["w"] / b, "s/b": p["s"] / b, "t/b": p["t"] / b}
        scale = b
    if not _inside(reg.box, vals):
        return None, f"{reg.name}: geometry outside the calibration box {reg.box}"
    if not _planes_clear(xs, extent, scale):
        return None, f"a plane edge is nearer than {MIN_PLANE_MARGIN:g} x {scale:.3g} m"
    if extrapolated:
        if reg.bound_extrapolated is None:
            return None, f"{reg.name}: formula is outside its published range"
        return reg.bound_extrapolated, f"{reg.name} (extrapolated, calibrated)"
    return reg.bound, f"{reg.name} (calibrated)"
