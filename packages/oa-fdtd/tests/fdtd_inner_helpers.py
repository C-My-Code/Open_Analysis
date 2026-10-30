# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Inner-layer test structures: a centred stripline whose copper layer lies between two slabs, and
a small closed via-through-planes box."""

from __future__ import annotations

import math

from oa_model.arrays import Points
from oa_model.em import (
    Boundaries,
    Box3,
    ConductorBody,
    Convergence,
    EMProblem,
    EMWavePort,
    FrequencyPlan,
    MaterialSlab,
    MeshSettings,
    RegionProvenance,
    Structure3D,
    ViaBody,
)
from oa_model.geometry import Polygon
from oa_model.materials import ConductorMaterial, ConstantDkDf, DielectricMaterial
from oa_testkit import builders as bld

C0 = 299_792_458.0
CU = ConductorMaterial(name="copper", conductivity=5.8e7)


def _rect(x0: float, y0: float, x1: float, y1: float) -> Polygon:
    return Polygon(exterior=Points(bld.rect_ring(x0, y0, x1, y1)))


def _body(bid: str, net: str, role: str, z0: float, z1: float, poly: Polygon) -> ConductorBody:
    return ConductorBody(
        id=bid,
        net=net,
        role=role,  # type: ignore[arg-type]
        layer="L1",
        z_bottom=z0,
        z_top=z1,
        polygons=(poly,),
    )


def _problem(
    name: str,
    st: Structure3D,
    ports: tuple[EMWavePort, ...],
    f_max: float,
    bnd: Boundaries,
    quality: str = "medium",
) -> EMProblem:
    return EMProblem(
        name=name,
        structure=st,
        ports=ports,
        boundaries=bnd,
        frequency=FrequencyPlan(f_min=0.0, f_max=f_max, n_points=41),
        mesh=MeshSettings(preset=quality, expert={"fdtd.copper": "pec"}),  # type: ignore[arg-type]
        convergence=Convergence(energy_decay_db=-60.0, max_timesteps=400_000),
    )


def stripline_two_slabs(
    length: float = 10e-3,
    er: float = 4.0,
    t: float = 35e-6,
    b: float = 0.4e-3,
    width: float = 0.2e-3,
    y_half: float = 2.0e-3,
    f_max: float = 20e9,
) -> EMProblem:
    """Centred stripline (lossless ``er``, PEC strip of thickness ``t``) whose copper layer is
    the z gap between two dielectric slabs, as in a real stackup; PEC planes and side walls."""
    zs0, zs1 = 0.5 * (b - t), 0.5 * (b + t)
    mat = DielectricMaterial(name="LAM", model=ConstantDkDf(dk=er, df=0.0))
    box = Box3(xmin=0.0, xmax=length, ymin=-y_half, ymax=y_half, zmin=0.0, zmax=b)
    st = Structure3D(
        bbox=box,
        materials=(mat, CU),
        slabs=(
            MaterialSlab(material="LAM", z_bottom=0.0, z_top=zs0),
            MaterialSlab(material="LAM", z_bottom=zs1, z_top=b),
        ),
        conductors=(
            _body("cb:S", "SIG", "signal", zs0, zs1, _rect(0.0, -width / 2, length, width / 2)),
        ),
        vias=(),
        provenance=RegionProvenance(),
    )
    ports = tuple(
        EMWavePort(
            name=n,
            face=f,  # type: ignore[arg-type]
            rect=(-y_half, y_half, 0.0, b),
            line_type="stripline",
            signal_conductors=("cb:S",),
        )
        for n, f in (("P1", "xmin"), ("P2", "xmax"))
    )
    bnd = Boundaries(
        xmin="absorbing", xmax="absorbing", ymin="pec", ymax="pec", zmin="pec", zmax="pec"
    )
    return _problem("stripline_two_slabs", st, ports, f_max, bnd)


def via_box(
    r_anti: float = 0.3e-3, er_lo: float = 3.9, er_hi: float = 4.4, f_max: float = 20e9
) -> EMProblem:
    """Closed lossless box: a signal via from a top trace through two inner planes with antipads
    (copper layers between slabs of two permittivities) to a bottom trace, two return vias."""
    cu, d = 35e-6, 0.2e-3
    z = [0.0]
    for h in (cu, d, cu, d, cu, d, cu):
        z.append(z[-1] + h)
    length, y_half, xv, yv = 3.0e-3, 1.5e-3, 1.5e-3, 0.0
    air = 0.3e-3
    box = Box3(xmin=0.0, xmax=length, ymin=-y_half, ymax=y_half, zmin=-air, zmax=z[-1] + air)
    lo = DielectricMaterial(name="LO", model=ConstantDkDf(dk=er_lo, df=0.0))
    hi = DielectricMaterial(name="HI", model=ConstantDkDf(dk=er_hi, df=0.0))
    ring = Points(bld.rect_ring(box.xmin, box.ymin, box.xmax, box.ymax))
    hole = Points(bld.circle_ring(xv, yv, r_anti, 24, clockwise=True))
    plane = Polygon(exterior=ring, holes=(hole,))
    w = 0.15e-3
    bodies = (
        _body("cb:B", "S", "signal", z[0], z[1], _rect(xv, -w / 2, length, w / 2)),
        _body("cb:P1", "GND", "reference", z[2], z[3], plane),
        _body("cb:P2", "GND", "reference", z[4], z[5], plane),
        _body("cb:T", "S", "signal", z[6], z[7], _rect(0.0, -w / 2, xv, w / 2)),
    )
    vias = [ViaBody(id="vb:S", net="S", role="signal", center=(xv, yv), z_bottom=z[0],
                    z_top=z[7], r_outer=0.125e-3, plating=25e-6)]  # fmt: skip
    for k, ang in enumerate((60.0, 120.0)):
        c = (xv + 0.9e-3 * math.cos(math.radians(ang)), yv + 0.9e-3 * math.sin(math.radians(ang)))
        vias.append(ViaBody(id=f"vb:G{k}", net="GND", role="reference", center=c, z_bottom=z[0],
                            z_top=z[7], r_outer=0.125e-3, plating=25e-6))  # fmt: skip
    st = Structure3D(
        bbox=box,
        materials=(lo, hi, CU),
        slabs=(
            MaterialSlab(material="LO", z_bottom=z[1], z_top=z[2]),
            MaterialSlab(material="HI", z_bottom=z[3], z_top=z[4]),
            MaterialSlab(material="LO", z_bottom=z[5], z_top=z[6]),
        ),
        conductors=bodies,
        vias=tuple(vias),
        provenance=RegionProvenance(),
    )
    ports = tuple(
        EMWavePort(
            name=n,
            face=f,  # type: ignore[arg-type]
            rect=(box.ymin, box.ymax, box.zmin, box.zmax),
            line_type="stripline",
            signal_conductors=(sid,),
        )
        for n, f, sid in (("P1", "xmin", "cb:T"), ("P2", "xmax", "cb:B"))
    )
    bnd = Boundaries(
        xmin="absorbing", xmax="absorbing", ymin="pec", ymax="pec", zmin="pec", zmax="pec"
    )
    return _problem("via_box", st, ports, f_max, bnd, quality="coarse")
