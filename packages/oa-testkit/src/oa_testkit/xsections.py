# SPDX-License-Identifier: GPL-3.0-or-later
"""``CrossSection`` fixtures with reference impedances (docs/research/02-impedance.md sec. 4).

Each ``XsCase`` couples a canonical ``CrossSection`` with expected values, the tolerance a solver
should meet against them, and the source of the numbers.

Provenance of the expected values (``XsCase.kind``):

``analytic``      exact closed-form (coax, wire over plane, two-wire line, Cohn zero-thickness
                  stripline and edge-coupled stripline, zero-thickness CPW). Evaluated with
                  :mod:`oa_testkit.analytic` (arithmetic-geometric-mean elliptic integrals,
                  checked against scipy and the research report's seed tables).
``formula_lock``  a published *approximation* (Hammerstad-Jensen 1980) evaluated by our own
                  transcription. Useful to lock a closed-form implementation to 1e-6 (call
                  :func:`oa_testkit.analytic.hammerstad_jensen`), but it is not physical truth and
                  carries "self-computed, needs independent check" in ``source``.

``expected`` keys: ``z0`` (single signal conductor), ``eeff`` (where a dielectric is present),
``z0e``/``z0o``/``zdiff``/``zcomm`` (coupled pairs), ``zdiff`` (balanced two-wire line).

Geometry notes (assumptions, flagged in the WP report):

- Zero-thickness conductors are approximated by traces of thickness ``THIN * reference length``
  (``XsTrapezoid`` requires ``thickness > 0``); ``rtol`` accounts for that. Planes use the
  zero-thickness ``XsPlane`` (explicitly allowed).
- Sections are built already canonical: ``u = 0`` is the midpoint of the signal bounding box and
  ``z = 0`` the lowest point of any conductor or dielectric.
- Round conductors: inner conductors are exact ``XsCircle``; the coax outer conductor is an
  annular ``XsPolygon`` with 1024 vertices per ring (equal-area radius, radius error < 1e-5).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from oa_model.arrays import Points
from oa_model.geometry import Polygon
from oa_model.materials import ConstantDkDf
from oa_model.xsection import (
    CrossSection,
    XsCircle,
    XsConductor,
    XsDielectric,
    XsPlane,
    XsPolygon,
    XsRegion,
    XsSlab,
    XsTrapezoid,
)

from . import analytic as an

__all__ = ["THIN", "XsCase", "all_cases", "get_case"]

THIN = 1e-4  # relative thickness used to approximate zero-thickness strips
_N_RING = 1024


@dataclass(frozen=True)
class XsCase:
    name: str
    xs: CrossSection
    expected: Mapping[str, float]
    rtol: float  # tolerance a solver must meet against `expected`
    source: str
    kind: Literal["analytic", "formula_lock"]
    note: str = ""

    @property
    def topology(self) -> str:
        return self.xs.topology_hint


# ---------------------------------------------------------------------------------------------
# Construction helpers
# ---------------------------------------------------------------------------------------------


def _tag(x: float) -> str:
    return f"{x:g}".replace(".", "p").replace("-", "m")


def _strip(
    id_: str, role: Literal["signal", "ground"], u: float, z: float, w: float, t: float
) -> XsConductor:
    return XsConductor(
        id=id_,
        role=role,
        shape=XsTrapezoid(u_center=u, z_bottom=z, w_bottom=w, w_top=w, thickness=t),
    )


def _plane(
    id_: str,
    z: float,
    *,
    t: float = 0.0,
    u_min: float | None = None,
    u_max: float | None = None,
) -> XsConductor:
    return XsConductor(
        id=id_, role="ground", shape=XsPlane(z_bottom=z, thickness=t, u_min=u_min, u_max=u_max)
    )


def _slab(z0: float, z1: float, er: float) -> XsDielectric:
    return XsDielectric(region=XsSlab(z_bottom=z0, z_top=z1), model=ConstantDkDf(dk=er, df=0.0))


def _ring(cx: float, cy: float, r: float, n: int = _N_RING) -> list[tuple[float, float]]:
    """CCW n-gon with vertices on the circle of radius r."""
    return [
        (cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n))
        for i in range(n)
    ]


def _equal_area_radius(r: float, n: int = _N_RING) -> float:
    x = 2 * math.pi / n
    return r * math.sqrt(x / math.sin(x))


# ---------------------------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------------------------

_HJ_SRC = (
    "Hammerstad & Jensen, 'Accurate models for microstrip computer-aided design', IEEE MTT-S Int. "
    "Microwave Symp. Dig., 1980, pp. 407-409 (zero-thickness Z0, eeff), formulas as in "
    "docs/research/02-impedance.md sec. 1.2; evaluated by oa_testkit.analytic.hammerstad_jensen. "
    "Self-computed, needs independent check (Wadell Handbook ch. 3 worked examples / SI9000)."
)


def _hj_case(u: float, er: float) -> XsCase:
    h = 0.2e-3
    w, t = u * h, THIN * h
    z0, eeff = an.hammerstad_jensen(u, er)
    xs = CrossSection(
        conductors=(
            _strip("s1", "signal", 0.0, h, w, t),
            _plane("g1", 0.0),
        ),
        dielectrics=(_slab(0.0, h, er),),
        topology_hint="microstrip",
        symmetric_about_u0=True,
    )
    return XsCase(
        name=f"hj_microstrip_u{_tag(u)}_er{_tag(er)}",
        xs=xs,
        expected={"z0": z0, "eeff": eeff},
        rtol=1.5e-2,  # HJ is ~0.2-1 % from exact quasi-static; strip is t = 1e-4 h, not 0
        source=_HJ_SRC,
        kind="formula_lock",
        note="Closed-form implementations should match to 1e-6; field solvers to ~1 %.",
    )


def _cohn_case(w_over_b: float, er: float) -> XsCase:
    b = 0.5e-3
    w, t = w_over_b * b, THIN * b
    xs = CrossSection(
        conductors=(
            _plane("g1", 0.0),
            _strip("s1", "signal", 0.0, b / 2 - t / 2, w, t),
            _plane("g2", b),
        ),
        dielectrics=(_slab(0.0, b, er),),
        topology_hint="stripline",
        symmetric_about_u0=True,
    )
    return XsCase(
        name=f"cohn_stripline_w{_tag(w_over_b)}_er{_tag(er)}",
        xs=xs,
        expected={"z0": an.cohn_stripline_z0(w_over_b, er), "eeff": er},
        rtol=5e-3,
        source=(
            "analytic: Z0 = eta0/(4 sqrt(er)) K(k)/K(k'), k = sech(pi W/(2b)); S. B. Cohn, "
            "'Characteristic Impedance of the Shielded-Strip Transmission Line', IRE Trans. "
            "MTT-2, July 1954, pp. 52-57 (zero thickness; strip modelled with t = 1e-4 b)"
        ),
        kind="analytic",
    )


def _edge_coupled_case(s_over_b: float, er: float = 1.0) -> XsCase:
    b, w_over_b = 0.5e-3, 0.5
    w, s, t = w_over_b * b, s_over_b * b, THIN * b
    pitch = (w + s) / 2
    z = an.cohn_edge_coupled_stripline(w_over_b, s_over_b, er)
    xs = CrossSection(
        conductors=(
            _plane("g1", 0.0),
            _strip("s1", "signal", -pitch, b / 2 - t / 2, w, t),
            _strip("s2", "signal", +pitch, b / 2 - t / 2, w, t),
            _plane("g2", b),
        ),
        dielectrics=(_slab(0.0, b, er),),
        topology_hint="edge_coupled_stripline",
        symmetric_about_u0=True,
    )
    return XsCase(
        name=f"edge_coupled_stripline_w0p5_s{_tag(s_over_b)}_er{_tag(er)}",
        xs=xs,
        expected={**z, "eeff": er},
        rtol=5e-3,
        source=(
            "analytic: Z0e,o = eta0/(4 sqrt(er)) K(k')/K(k), ke = tanh(pi W/2b) tanh(pi (W+S)/2b), "
            "ko = tanh(pi W/2b) coth(pi (W+S)/2b); S. B. Cohn, 'Shielded Coupled-Strip "
            "Transmission Line', IRE Trans. MTT-3, Oct. 1955, pp. 29-38 (zero thickness, centred; "
            "strips modelled with t = 1e-4 b). zdiff = 2 Z0o, zcomm = Z0e/2."
        ),
        kind="analytic",
    )


def _cpw_case(er: float, h: float | None) -> XsCase:
    s, g = 1e-3, 0.5e-3
    t = THIN * s
    sub_h = 5e-3 if h is None else h
    z_c = 0.0 if er == 1.0 else sub_h  # conductors sit on the substrate top surface
    half = s / 2 + g
    conductors = (
        _strip("s1", "signal", 0.0, z_c, s, t),
        _plane("g1", z_c, t=t, u_max=-half),
        _plane("g2", z_c, t=t, u_min=+half),
    )
    dielectrics = () if er == 1.0 else (_slab(0.0, sub_h, er),)
    z0, eeff = an.cpw_z0_eeff(s, g, er, None if er == 1.0 else sub_h)
    xs = CrossSection(
        conductors=conductors,
        dielectrics=dielectrics,
        topology_hint="cpw",
        symmetric_about_u0=True,
    )
    name = "cpw_s1_g0p5_air" if er == 1.0 else f"cpw_s1_g0p5_er{_tag(er)}_h{_tag(sub_h * 1e3)}mm"
    return XsCase(
        name=name,
        xs=xs,
        expected={"z0": z0, "eeff": eeff},
        rtol=5e-3,
        source=(
            "analytic: Z0 = 30 pi/sqrt(eeff) K(k0')/K(k0), k0 = S/(S+2G), "
            "eeff = 1 + (er-1)/2 K(k1)/K(k1') K(k0')/K(k0), k1 = sinh(pi S/4h)/sinh(pi (S+2G)/4h); "
            "conformal mapping, Ghione & Naldi, IEEE Trans. MTT-35(3), 1987, pp. 260-267 "
            "(zero thickness, infinite side grounds, no bottom ground; strip t = 1e-4 S)"
        ),
        kind="analytic",
        note="S = 1 mm, G = 0.5 mm. Side grounds are half-planes (u_min/u_max = None).",
    )


def _coax_case(er: float) -> XsCase:
    a = 0.5e-3
    b = a * math.e  # ln(b/a) = 1
    wall = 0.5e-3
    r_out = b + wall
    bv = _equal_area_radius(b)
    zc = r_out  # lowest point of the section is the bottom of the outer conductor
    outer = XsPolygon(
        polygon=Polygon(
            exterior=Points(_ring(0.0, zc, r_out)),
            holes=(Points(_ring(0.0, zc, bv)[::-1]),),
        )
    )
    dielectrics = (
        ()
        if er == 1.0
        else (
            XsDielectric(
                region=XsRegion(polygon=Polygon(exterior=Points(_ring(0.0, zc, bv)))),
                model=ConstantDkDf(dk=er, df=0.0),
                priority=10,
            ),
        )
    )
    xs = CrossSection(
        conductors=(
            XsConductor(id="s1", role="signal", shape=XsCircle(u_center=0.0, z_center=zc, radius=a)),
            XsConductor(id="g1", role="ground", shape=outer),
        ),
        dielectrics=dielectrics,
        topology_hint="coax",
        symmetric_about_u0=True,
    )
    return XsCase(
        name="coax_lnba1_air" if er == 1.0 else f"coax_lnba1_er{_tag(er)}",
        xs=xs,
        expected={"z0": an.coax_z0(math.e, er), "eeff": er},
        rtol=1e-4,
        source=(
            "analytic: Z0 = eta0/(2 pi sqrt(er)) ln(b/a), ln(b/a) = 1 "
            "(textbook coaxial-line result, e.g. Pozar, Microwave Engineering, coaxial line; 59.958 ohm in air)"
        ),
        kind="analytic",
        note="a = 0.5 mm, b = a e, outer wall 0.5 mm.",
    )


def _wire_over_plane_case(h_over_r: float) -> XsCase:
    r = 0.25e-3
    h = h_over_r * r
    xs = CrossSection(
        conductors=(
            XsConductor(id="s1", role="signal", shape=XsCircle(u_center=0.0, z_center=h, radius=r)),
            _plane("g1", 0.0),
        ),
        dielectrics=(),
        topology_hint="wire_over_plane",
        symmetric_about_u0=True,
    )
    return XsCase(
        name=f"wire_over_plane_h{_tag(h_over_r)}r",
        xs=xs,
        expected={"z0": an.wire_over_plane_z0(h_over_r), "eeff": 1.0},
        rtol=1e-4,
        source=(
            "analytic: Z0 = eta0/(2 pi sqrt(er)) acosh(h/r) (method of images; "
            "textbook result, e.g. Ramo, Whinnery & Van Duzer, Fields and Waves in "
            "Communication Electronics, two-wire/image-theory sections)"
        ),
        kind="analytic",
        note="Air, r = 0.25 mm; the plane is infinite and zero thickness.",
    )


def _two_wire_case(d_over_diam: float) -> XsCase:
    r = 0.25e-3
    big_d = d_over_diam * 2 * r
    xs = CrossSection(
        conductors=(
            XsConductor(id="s1", role="signal", shape=XsCircle(u_center=-big_d / 2, z_center=r, radius=r)),
            XsConductor(id="s2", role="signal", shape=XsCircle(u_center=+big_d / 2, z_center=r, radius=r)),
        ),
        dielectrics=(),
        topology_hint="general",
        symmetric_about_u0=True,
    )
    return XsCase(
        name=f"two_wire_D{_tag(d_over_diam)}d",
        xs=xs,
        expected={"zdiff": an.two_wire_z0(d_over_diam), "eeff": 1.0},
        rtol=1e-4,
        source=(
            "analytic: Z0 = eta0/(pi sqrt(er)) acosh(D/d) (balanced line; "
            "textbook result, e.g. Ramo, Whinnery & Van Duzer; Pozar, Microwave Engineering)"
        ),
        kind="analytic",
        note="Air, wire diameter 0.5 mm, centre spacing D; Z0 is the line (differential) impedance.",
    )


def _build() -> tuple[XsCase, ...]:
    cases: list[XsCase] = []
    # Hammerstad-Jensen seed table (research 02 sec. 4.3)
    for u, er in [(0.5, 1.0), (0.5, 4.3), (1.0, 4.3), (1.9, 4.3), (2.0, 4.3), (2.0, 10.2), (4.0, 4.3)]:
        cases.append(_hj_case(u, er))
    # Cohn stripline (research 02 sec. 1.7)
    for w in (0.1, 0.25, 0.5, 1.0, 2.0):
        cases.append(_cohn_case(w, 1.0))
    cases.append(_cohn_case(0.5, 4.3))
    # Edge-coupled stripline (research 02 sec. 1.9)
    for s in (0.1, 0.25, 0.5):
        cases.append(_edge_coupled_case(s))
    cases.append(_edge_coupled_case(0.25, 4.3))
    # CPW S=1, G=0.5
    cases.append(_cpw_case(1.0, None))
    cases.append(_cpw_case(9.8, 5e-3))
    # Round conductors
    cases += [_coax_case(1.0), _coax_case(2.1)]
    cases += [_wire_over_plane_case(2.0), _wire_over_plane_case(5.0)]
    cases.append(_two_wire_case(2.5))
    names = [c.name for c in cases]
    assert len(set(names)) == len(names)
    return tuple(cases)


_CASES: tuple[XsCase, ...] | None = None


def all_cases() -> tuple[XsCase, ...]:
    """Every cross-section case (built once, immutable)."""
    global _CASES
    if _CASES is None:
        _CASES = _build()
    return _CASES


def get_case(name: str) -> XsCase:
    for c in all_cases():
        if c.name == name:
            return c
    raise KeyError(f"unknown cross-section case {name!r}; known: {[c.name for c in all_cases()]}")
