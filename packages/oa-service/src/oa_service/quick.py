# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build a canonical ``CrossSection`` from the ``quick`` topology parameters of `impedance.xs`."""

from __future__ import annotations

from oa_model.materials import ConstantDkDf
from oa_model.xsection import (
    CrossSection,
    XsConductor,
    XsDielectric,
    XsPlane,
    XsSlab,
    XsTrapezoid,
)
from oa_service.errors import ServiceError
from oa_service.models import QuickXs

__all__ = ["quick_xsection"]


def _strip(id_: str, u: float, z: float, w: float, t: float) -> XsConductor:
    return XsConductor(
        id=id_,
        role="signal",
        shape=XsTrapezoid(u_center=u, z_bottom=z, w_bottom=w, w_top=w, thickness=t),
    )


def _plane(id_: str, z: float) -> XsConductor:
    return XsConductor(id=id_, role="ground", shape=XsPlane(z_bottom=z, thickness=0.0))


def _need(q: QuickXs, field: str) -> float:
    v = getattr(q, field)
    if v is None:
        raise ServiceError(
            "SETUP_INVALID",
            f"Topology {q.topology} needs `{field}`.",
            path=f"/quick/{field}",
            hint=f"Add {field} (metres or a string such as '5mil').",
        )
    return float(v)


def quick_xsection(q: QuickXs) -> CrossSection:
    """Canonical section for the closed-form topologies. CPW variants are not supported here."""
    w, h, t = q.w, q.h, q.t
    model = ConstantDkDf(dk=q.er, df=q.df)
    topo = q.topology
    coupled = topo.startswith("edge_coupled")
    if coupled:
        s = _need(q, "s")
        centres = [-(w + s) / 2, (w + s) / 2]
    else:
        centres = [0.0]
    if topo in ("microstrip", "edge_coupled_microstrip"):
        z_tr = h
        conductors = [_plane("g1", 0.0)]
        slabs = [XsSlab(z_bottom=0.0, z_top=h)]
        top = h + t
    elif topo == "embedded_microstrip":
        h2 = _need(q, "h2")
        z_tr = h
        conductors = [_plane("g1", 0.0)]
        top = h + t + h2
        slabs = [XsSlab(z_bottom=0.0, z_top=top)]
    elif topo in ("stripline", "offset_stripline", "edge_coupled_stripline"):
        h2 = _need(q, "h2") if topo == "offset_stripline" else h
        z_tr = h
        top = h + t + h2
        conductors = [_plane("g1", 0.0), _plane("g2", top)]
        slabs = [XsSlab(z_bottom=0.0, z_top=top)]
    else:
        raise ServiceError(
            "UNSUPPORTED_FEATURE",
            f"Quick topology {topo!r} is not supported; give an inline `xsection`.",
            path="/quick/topology",
            hint="Use microstrip, embedded_microstrip, stripline, offset_stripline or the "
            "edge_coupled_* variants, or pass a full oa-xsection/1 document.",
        )
    sigs = [_strip(f"s{i + 1}", u, z_tr, w, t) for i, u in enumerate(centres)]
    xs = CrossSection(
        conductors=(*sigs, *conductors),
        dielectrics=tuple(XsDielectric(region=s_, model=model) for s_ in slabs),
        topology_hint=topo,
        symmetric_about_u0=True,
    )
    return CrossSection.canonicalize(xs)
