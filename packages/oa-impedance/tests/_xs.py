# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cross-section builders for the impedance tests.

Unlike ``oa_testkit.xsections`` (zero-thickness strips approximated by ``t = 1e-4 * ref`` with
rtol 1e-2) these build *true* thin strips (``t = 1e-6 * ref`` by default), so a field solver can be
validated against the exact zero-thickness formulas at a few 1e-3.
"""

from __future__ import annotations

from typing import Literal

from oa_model.materials import ConstantDkDf
from oa_model.xsection import (
    CrossSection,
    XsConductor,
    XsDielectric,
    XsPlane,
    XsSlab,
    XsTrapezoid,
)

THIN = 1e-6


def strip(
    id_: str, role: Literal["signal", "ground"], u: float, z: float, w: float, t: float
) -> XsConductor:
    return XsConductor(
        id=id_,
        role=role,
        shape=XsTrapezoid(u_center=u, z_bottom=z, w_bottom=w, w_top=w, thickness=t),
    )


def plane(id_: str, z: float, t: float = 0.0, **kw: float | None) -> XsConductor:
    return XsConductor(id=id_, role="ground", shape=XsPlane(z_bottom=z, thickness=t, **kw))  # type: ignore[arg-type]


def slab(z0: float, z1: float, er: float, df: float = 0.0) -> XsDielectric:
    return XsDielectric(region=XsSlab(z_bottom=z0, z_top=z1), model=ConstantDkDf(dk=er, df=df))


def stripline(
    w_over_b: float, er: float = 1.0, t_over_b: float = THIN, b: float = 1e-3
) -> CrossSection:
    w, t = w_over_b * b, t_over_b * b
    return CrossSection(
        conductors=(
            plane("g1", 0.0),
            strip("s1", "signal", 0.0, b / 2 - t / 2, w, t),
            plane("g2", b),
        ),
        dielectrics=(slab(0.0, b, er),),
        topology_hint="stripline",
        symmetric_about_u0=True,
    )


def edge_coupled_stripline(
    w_over_b: float, s_over_b: float, er: float = 1.0, t_over_b: float = THIN, b: float = 1e-3
) -> CrossSection:
    w, s, t = w_over_b * b, s_over_b * b, t_over_b * b
    pitch = (w + s) / 2
    return CrossSection(
        conductors=(
            plane("g1", 0.0),
            strip("s1", "signal", -pitch, b / 2 - t / 2, w, t),
            strip("s2", "signal", +pitch, b / 2 - t / 2, w, t),
            plane("g2", b),
        ),
        dielectrics=(slab(0.0, b, er),),
        topology_hint="edge_coupled_stripline",
        symmetric_about_u0=True,
    )


def microstrip(
    u: float,
    er: float,
    t_over_h: float = THIN,
    h: float = 1e-3,
    df: float = 0.0,
    cover: float = 0.0,
) -> CrossSection:
    """Surface microstrip; ``cover > 0`` adds a same-er layer of that height above the trace."""
    w, t = u * h, t_over_h * h
    top = h if cover == 0.0 else h + t + cover
    return CrossSection(
        conductors=(plane("g1", 0.0), strip("s1", "signal", 0.0, h, w, t)),
        dielectrics=(slab(0.0, top, er, df),),
        topology_hint="microstrip" if cover == 0.0 else "embedded_microstrip",
        symmetric_about_u0=True,
    )


def edge_coupled_microstrip(
    u: float, g: float, er: float, t_over_h: float = THIN, h: float = 1e-3
) -> CrossSection:
    w, s, t = u * h, g * h, t_over_h * h
    pitch = (w + s) / 2
    return CrossSection(
        conductors=(
            plane("g1", 0.0),
            strip("s1", "signal", -pitch, h, w, t),
            strip("s2", "signal", +pitch, h, w, t),
        ),
        dielectrics=(slab(0.0, h, er),),
        topology_hint="edge_coupled_microstrip",
        symmetric_about_u0=True,
    )


def cpw_halfspace(
    er: float, s: float = 1e-3, g: float = 0.5e-3, depth: float = 40e-3
) -> CrossSection:
    """Zero-thickness CPW on a (practically infinite) dielectric half space."""
    t = THIN * s
    half = s / 2 + g
    return CrossSection(
        conductors=(
            strip("s1", "signal", 0.0, depth, s, t),
            plane("g1", depth, t, u_max=-half),
            plane("g2", depth, t, u_min=+half),
        ),
        dielectrics=(slab(0.0, depth, er),),
        topology_hint="cpw",
        symmetric_about_u0=True,
    )


def homogeneous_pair(er: float = 3.0) -> CrossSection:
    """Edge-coupled microstrip pair in an everywhere-homogeneous medium (background_dk = er)."""
    h, w, s, t = 0.2e-3, 0.3e-3, 0.2e-3, 35e-6
    pitch = (w + s) / 2
    return CrossSection(
        conductors=(
            plane("g1", 0.0),
            strip("s1", "signal", -pitch, h, w, t),
            strip("s2", "signal", +pitch, h, w, t),
        ),
        dielectrics=(),
        background_dk=er,
        symmetric_about_u0=True,
    )


def pcb_microstrip(pair: bool = False, mask: bool = False, df: float = 0.02) -> CrossSection:
    """Typical PCB microstrip (single or edge-coupled pair), 35 um trapezoid copper."""
    h, w, s, t, er = 0.1e-3, 0.15e-3, 0.15e-3, 35e-6, 4.3
    pitch = (w + s) / 2
    centers = [-pitch, pitch] if pair else [0.0]
    cond = [plane("g1", -t, t)]
    for k, uc in enumerate(centers):
        from oa_model.xsection import XsTrapezoid as T

        cond.append(
            XsConductor(
                id=f"s{k + 1}",
                role="signal",
                shape=T(u_center=uc, z_bottom=h, w_bottom=w, w_top=w - 20e-6, thickness=t),
            )
        )
    diel = [slab(0.0, h, er, df)]
    if mask:
        diel.append(slab(h, h + t + 20e-6, 3.5, df))
    return CrossSection(
        conductors=tuple(cond),
        dielectrics=tuple(diel),
        topology_hint="general",
        symmetric_about_u0=True,
    )
