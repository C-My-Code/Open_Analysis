# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``EMProblem`` builders for the B-EM benchmark set (BENCHMARKS.md section 3).

All structures are built directly as ``Structure3D`` (no board), solver-neutral, so that oa-fdtd and
the openEMS adapter solve the same object:

- :func:`microstrip_thru` (B-EM-01), :func:`stripline_exact` / :func:`stripline_lossy` (B-EM-02),
- :func:`diff_via` (B-EM-03), :func:`cpwg_launch` (B-EM-04),
- :func:`patch_antenna` (B-EM-05a), :func:`msl_notch` (B-EM-06b).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from oa_model.arrays import Points
from oa_model.em import (
    Boundaries,
    Box3,
    ConductorBody,
    Convergence,
    EMLumpedPort,
    EMProblem,
    EMTerminalGeom,
    EMWavePort,
    FrequencyPlan,
    MaterialSlab,
    MeshSettings,
    RegionProvenance,
    Structure3D,
    ViaBody,
)
from oa_model.geometry import Polygon
from oa_model.materials import (
    ConductorMaterial,
    ConstantDkDf,
    DielectricMaterial,
    DjordjevicSarkar,
)
from oa_testkit import builders as bld
from oa_testkit.emproblems import microstrip_thru_problem, stripline_thru_problem

CU = 35e-6
CU_MAT = ConductorMaterial(name="copper", conductivity=5.8e7)

__all__ = [
    "cpwg_launch",
    "diff_via",
    "microstrip_thru",
    "msl_notch",
    "patch_antenna",
    "stripline_exact",
    "stripline_lossy",
]


def rect(x0: float, y0: float, x1: float, y1: float) -> Polygon:
    return Polygon(exterior=Points(bld.rect_ring(x0, y0, x1, y1)))


def _body(
    bid: str,
    net: str | None,
    role: str,
    layer: str,
    z0: float,
    z1: float,
    polys: Sequence[Polygon],
) -> ConductorBody:
    return ConductorBody(
        id=bid,
        net=net,
        role=role,  # type: ignore[arg-type]
        layer=layer,
        z_bottom=z0,
        z_top=z1,
        polygons=tuple(polys),
    )


def _plane(
    bid: str,
    layer: str,
    z0: float,
    z1: float,
    box: Box3,
    holes: Sequence[tuple[float, float, float]] = (),
) -> ConductorBody:
    poly = Polygon(
        exterior=Points(bld.rect_ring(box.xmin, box.ymin, box.xmax, box.ymax)),
        holes=tuple(Points(bld.circle_ring(cx, cy, r, 24, clockwise=True)) for cx, cy, r in holes),
    )
    return _body(bid, "GND", "reference", layer, z0, z1, [poly])


def _structure(
    box: Box3,
    mats: Sequence[DielectricMaterial | ConductorMaterial],
    slabs: Sequence[MaterialSlab],
    conductors: Sequence[ConductorBody],
    vias: Sequence[ViaBody] = (),
) -> Structure3D:
    return Structure3D(
        bbox=box,
        materials=tuple(mats),
        slabs=tuple(slabs),
        conductors=tuple(conductors),
        vias=tuple(vias),
        provenance=RegionProvenance(),
    )


def _problem(
    name: str,
    st: Structure3D,
    ports: Sequence[EMWavePort | EMLumpedPort],
    bnd: Boundaries,
    f_max: float,
    n_points: int,
    quality: str,
    *,
    energy_db: float = -50.0,
    f_min: float = 0.0,
    max_steps: int = 1_000_000,
    copper: str | None = None,
    pulse: str | None = None,
    cpw: float | None = None,
) -> EMProblem:
    expert: dict[str, str] = {}
    if copper:
        expert["fdtd.copper"] = copper
    if pulse:
        expert["fdtd.pulse"] = pulse
    return EMProblem(
        name=name,
        structure=st,
        ports=tuple(ports),
        boundaries=bnd,
        frequency=FrequencyPlan(f_min=f_min, f_max=f_max, n_points=n_points),
        mesh=MeshSettings(preset=quality, cells_per_wavelength=cpw, expert=expert),  # type: ignore[arg-type]
        convergence=Convergence(energy_decay_db=energy_db, max_timesteps=max_steps),
    )


# ---------------------------------------------------------------------------------- B-EM-01
def microstrip_thru(
    f_max: float = 20e9,
    n_points: int = 100,
    quality: str = "medium",
    length: float = 25e-3,
    energy_db: float = -70.0,
) -> EMProblem:
    """The testkit 25 mm microstrip thru with wave ports on the x faces."""
    p = microstrip_thru_problem(length=length, f_max=f_max, n_points=n_points)
    box = p.structure.bbox
    trace = next(c for c in p.structure.conductors if c.net == "SIG")
    ports = tuple(
        EMWavePort(
            name=name,
            face=face,  # type: ignore[arg-type]
            rect=(box.ymin, box.ymax, box.zmin, box.zmax),
            line_type="microstrip",
            signal_conductors=(trace.id,),
        )
        for name, face in (("P1", "xmin"), ("P2", "xmax"))
    )
    mesh = p.mesh.model_copy(update={"preset": quality})
    conv = p.convergence.model_copy(update={"energy_decay_db": energy_db})
    return p.model_copy(update={"ports": ports, "mesh": mesh, "convergence": conv})


# ---------------------------------------------------------------------------------- B-EM-02
def stripline_lossy(
    f_max: float = 20e9,
    n_points: int = 100,
    quality: str = "medium",
    length: float = 25e-3,
    energy_db: float = -50.0,
) -> EMProblem:
    """The testkit FR-4 stripline thru (35 um copper), -50 dB energy criterion."""
    p = stripline_thru_problem(length=length, f_max=f_max, n_points=n_points)
    mesh = p.mesh.model_copy(update={"preset": quality})
    conv = p.convergence.model_copy(update={"energy_decay_db": energy_db})
    return p.model_copy(update={"mesh": mesh, "convergence": conv})


def stripline_exact(
    f_max: float = 20e9,
    n_points: int = 100,
    quality: str = "medium",
    length: float = 25e-3,
    er: float = 4.0,
    width: float = 0.2e-3,
    b: float = 0.4e-3,
    y_half: float = 2.0e-3,
    cpw: float | None = None,
    pulse: str = "gauss",
) -> EMProblem:
    """Centred stripline, lossless homogeneous ``er``, PEC walls and 5 um PEC strip (the thin-strip
    limit of Cohn's formula)."""
    t = 5e-6
    mat = DielectricMaterial(name="LOSSLESS", model=ConstantDkDf(dk=er, df=0.0))
    box = Box3(xmin=0.0, xmax=length, ymin=-y_half, ymax=y_half, zmin=0.0, zmax=b)
    strip = _body(
        "cb:S",
        "SIG",
        "signal",
        "L2",
        0.5 * (b - t),
        0.5 * (b + t),
        [rect(0.0, -width / 2, length, width / 2)],
    )
    st = _structure(
        box,
        [mat, CU_MAT],
        [MaterialSlab(material="LOSSLESS", z_bottom=0.0, z_top=b)],
        [
            _plane("cb:L1", "L1", -35e-6, 0.0, box),
            strip,
            _plane("cb:L3", "L3", b, b + 35e-6, box),
        ],
    )
    ports = [
        EMWavePort(
            name=n,
            face=f,  # type: ignore[arg-type]
            rect=(-y_half, y_half, 0.0, b),
            line_type="stripline",
            signal_conductors=("cb:S",),
        )
        for n, f in (("P1", "xmin"), ("P2", "xmax"))
    ]
    bnd = Boundaries(
        xmin="absorbing", xmax="absorbing", ymin="pec", ymax="pec", zmin="pec", zmax="pec"
    )
    return _problem(
        "stripline_exact",
        st,
        ports,
        bnd,
        f_max,
        n_points,
        quality,
        copper="pec",
        pulse=pulse,
        cpw=cpw,
    )


def cohn_z0(er: float, width: float, b: float) -> float:
    """Characteristic impedance [ohm] of a zero-thickness centred stripline (Cohn 1955, exact)."""
    k = 1.0 / math.cosh(math.pi * width / (2.0 * b))
    from scipy.special import ellipk

    kp = math.sqrt(1.0 - k * k)
    return float(30.0 * math.pi / math.sqrt(er) * ellipk(kp**2) / ellipk(k**2))


# ---------------------------------------------------------------------------------- B-EM-03
def _stack8(
    total: float = 1.6e-3, cu: float = CU
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """(copper z-ranges L1..L8 bottom-up so that L1 is the top, dielectric ranges)."""
    n_d = 7
    d = (total - 8 * cu) / n_d
    cop, die = [], []
    z = 0.0
    for k in range(8):
        cop.append((z, z + cu))
        z += cu
        if k < 7:
            die.append((z, z + d))
            z += d
    return cop, die  # index 0 = bottom copper (L8) ... 7 = top copper (L1)


def diff_via(
    f_max: float = 40e9,
    n_points: int = 80,
    quality: str = "medium",
    stub: float = 0.0,
    length: float = 5.0e-3,
    air: float = 0.8e-3,
    half: str = "odd",
    straight: bool = False,
    cpw: float | None = None,
    r_anti: float = 0.3e-3,
    cage_deg: tuple[float, ...] = (0.0, 40.0, 54.0, 68.0, 82.0, 98.0, 112.0, 126.0, 140.0, 180.0),
) -> EMProblem:
    """Differential via L1 -> L8 through 1.6 mm (0.25 mm drill, antipads, return vias).

    Diff microstrip pair (0.15 mm line, 0.8 mm pitch) on L1 into the via at x = length/2, and on
    L8 out of it. The structure is mirror symmetric about y = 0, so the differential (odd) mode is
    the half domain y >= 0 with a PEC wall at y = 0 and the common (even) mode the same with a PMC
    wall: ``half`` selects one; a single-mode wave port (the modal impedance of the half is
    ``Zdiff / 2``) gives ``SDD`` (odd) or ``SCC`` (even) exactly. A multi-mode port would need a
    non-local modal load (oa-fdtd loads each mode port with a local sheet, which cannot be
    orthogonal for two modes on the same plane). ``stub`` [m] extends the plated span below L8
    (a via stub); ``straight`` replaces the via by a straight L1 line (calibration).
    Five return vias on a half circle of radius 1.1 mm about the pair centre (stitching ring).
    """
    cop, die = _stack8()
    z_bot, z_top = 0.0, cop[-1][1]
    pitch, w = 0.8e-3, 0.15e-3
    y_sig = pitch / 2
    xv = 0.5 * length
    y_half = 1.8e-3
    box = Box3(
        xmin=0.0,
        xmax=length,
        ymin=0.0,
        ymax=y_half,
        zmin=z_bot - air - stub,
        zmax=z_top + air,
    )
    mat = DielectricMaterial(name="LAM", model=DjordjevicSarkar(dk=3.9, df=0.012, f_ref=1e9))
    slabs = [MaterialSlab(material="LAM", z_bottom=a, z_top=b) for a, b in die]
    r_drill = 0.125e-3
    planes = []
    for k in range(1, 7):  # inner layers L7..L2 are planes with an antipad around the signal via
        z0, z1 = cop[k]
        holes = [] if straight else [(xv, y_sig, r_anti)]
        planes.append(_plane(f"cb:P{k}", f"L{8 - k}", z0, z1, box, holes))
    zt0, zt1 = cop[7]
    zb0, zb1 = cop[0]
    top_end = length if straight else xv
    traces = [
        _body(
            "cb:T0",
            "S0",
            "signal",
            "L1",
            zt0,
            zt1,
            [rect(0.0, y_sig - w / 2, top_end, y_sig + w / 2)],
        )
    ]
    if not straight:
        traces.append(
            _body(
                "cb:B0",
                "S0",
                "signal",
                "L8",
                zb0,
                zb1,
                [rect(xv, y_sig - w / 2, length, y_sig + w / 2)],
            )
        )
    vias: list[ViaBody] = []
    if not straight:
        vias.append(
            ViaBody(
                id="vb:S0",
                net="S0",
                role="signal",
                center=(xv, y_sig),
                z_bottom=z_bot - stub,
                z_top=z_top,
                r_outer=r_drill,
                plating=25e-6,
            )
        )
        ring = [  # stitching vias on a half circle of radius 1.1 mm about the pair centre
            (1.1e-3 * math.cos(math.radians(t)), 1.1e-3 * math.sin(math.radians(t)))
            for t in cage_deg
        ]
        for k, (dx, y) in enumerate(ring):
            vias.append(
                ViaBody(
                    id=f"vb:G{k}",
                    net="GND",
                    role="reference",
                    center=(xv + dx, y),
                    z_bottom=z_bot,
                    z_top=z_top,
                    r_outer=r_drill,
                    plating=25e-6,
                )
            )
    st = _structure(box, [mat, CU_MAT], [*slabs], planes + traces, vias)
    out_id = "cb:T0" if straight else "cb:B0"
    ports = [
        EMWavePort(
            name=n,
            face=f,  # type: ignore[arg-type]
            rect=(0.0, y_half, box.zmin, box.zmax),
            line_type="microstrip",
            signal_conductors=(sid,),
        )
        for n, f, sid in (("P1", "xmin", "cb:T0"), ("P2", "xmax", out_id))
    ]
    wall = "pec" if half == "odd" else "pmc"
    bnd = Boundaries(
        xmin="absorbing",
        xmax="absorbing",
        ymin=wall,  # type: ignore[arg-type]
        ymax="absorbing",
        zmin="absorbing",
        zmax="absorbing",
    )
    return _problem(
        # -60 dB: at -40 dB the truncated low-frequency tail moves SDD21 by 0.15 dB below 3 GHz
        # (-60 is within 0.02 dB of -70 above 2 GHz)
        f"diff_via_8l_{half}",
        st,
        ports,
        bnd,
        f_max,
        n_points,
        quality,
        energy_db=-60.0,
        cpw=cpw,
    )


# ---------------------------------------------------------------------------------- B-EM-04
def cpwg_launch(
    f_max: float = 40e9,
    n_points: int = 80,
    quality: str = "medium",
    length: float = 6.0e-3,
    fence: bool = True,
) -> EMProblem:
    """CPWG on 0.254 mm laminate (dk 3.66, df 0.0037) with a via fence at lambda/10 (40 GHz) pitch
    and a 0.5 mm wider launch pad at each end; two CPWG wave ports."""
    h = 0.254e-3
    mat = DielectricMaterial(name="LAMN", model=ConstantDkDf(dk=3.66, df=0.0037, f_ref=10e9))
    y_half = 2.5e-3
    air = 1.2e-3
    box = Box3(xmin=0.0, xmax=length, ymin=-y_half, ymax=y_half, zmin=0.0, zmax=h + CU + air)
    w, g = 0.34e-3, 0.20e-3  # line / gap (about 50 ohm)
    w_pad, g_pad, l_pad = 0.50e-3, 0.30e-3, 0.5e-3
    gnd_in = w / 2 + g
    # top copper: signal strip (with pads), two coplanar ground pours
    strip = [
        rect(0.0, -w_pad / 2, l_pad, w_pad / 2),
        rect(l_pad, -w / 2, length - l_pad, w / 2),
        rect(length - l_pad, -w_pad / 2, length, w_pad / 2),
    ]
    gnd_polys = []
    for s in (-1, 1):
        y0, y1 = (gnd_in, y_half) if s > 0 else (-y_half, -gnd_in)
        y0p, y1p = (w_pad / 2 + g_pad, y_half) if s > 0 else (-y_half, -(w_pad / 2 + g_pad))
        gnd_polys += [
            rect(0.0, y0p, l_pad, y1p),
            rect(l_pad, y0, length - l_pad, y1),
            rect(length - l_pad, y0p, length, y1p),
        ]
    top = (h, h + CU)
    bodies = [
        _plane("cb:L2", "L2", -CU, 0.0, box),
        _body("cb:SIG", "SIG", "signal", "L1", *top, strip),
        _body("cb:GTOP", "GND", "reference", "L1", *top, gnd_polys),
    ]
    vias = []
    if fence:
        pitch = 0.45e-3  # lambda/10 at 40 GHz in eps_eff ~ 2.5
        n = int((length - 2 * l_pad) / pitch)
        x0 = 0.5 * (length - (n - 1) * pitch)
        for i in range(n):
            for s in (-1, 1):
                vias.append(
                    ViaBody(
                        id=f"vb:F{i}{'p' if s > 0 else 'n'}",
                        net="GND",
                        role="reference",
                        center=(x0 + i * pitch, s * (gnd_in + 0.45e-3)),
                        z_bottom=-CU,
                        z_top=h + CU,
                        r_outer=0.1e-3,
                        plating=25e-6,
                    )
                )
    st = _structure(
        box,
        [mat, CU_MAT],
        [MaterialSlab(material="LAMN", z_bottom=0.0, z_top=h)],
        bodies,
        vias,
    )
    ports = [
        EMWavePort(
            name=n,
            face=f,  # type: ignore[arg-type]
            rect=(-y_half, y_half, box.zmin, box.zmax),
            line_type="cpwg",
            signal_conductors=("cb:SIG",),
        )
        for n, f in (("P1", "xmin"), ("P2", "xmax"))
    ]
    bnd = Boundaries(
        xmin="absorbing", xmax="absorbing", ymin="pec", ymax="pec", zmin="pec", zmax="absorbing"
    )
    return _problem("cpwg_launch", st, ports, bnd, f_max, n_points, quality, energy_db=-40.0)


# ---------------------------------------------------------------------------------- B-EM-05a
def patch_antenna(
    f_max: float = 3.0e9, n_points: int = 121, quality: str = "medium", f_min: float = 1.8e9
) -> EMProblem:
    """The openEMS "Simple Patch Antenna" tutorial geometry: 32 x 40 mm patch on 60 x 60 x 1.524 mm
    (eps_r 3.38) over a ground plane, lumped 50 ohm feed 6 mm off-centre along x, open air box."""
    h = 1.524e-3
    mat = DielectricMaterial(name="RO4003", model=ConstantDkDf(dk=3.38, df=0.0, f_ref=2.4e9))
    sub = 60e-3
    pw, pl = 32e-3, 40e-3
    pad = 45e-3
    box = Box3(
        xmin=-sub / 2 - pad,
        xmax=sub / 2 + pad,
        ymin=-sub / 2 - pad,
        ymax=sub / 2 + pad,
        zmin=-pad,
        zmax=h + 2 * pad,
    )
    slab = MaterialSlab(
        material="RO4003",
        z_bottom=0.0,
        z_top=h,
        footprint=rect(-sub / 2, -sub / 2, sub / 2, sub / 2),
    )
    gnd = _body(
        "cb:GND",
        "GND",
        "reference",
        "L2",
        -35e-6,
        0.0,
        [rect(-sub / 2, -sub / 2, sub / 2, sub / 2)],
    )
    patch = _body(
        "cb:PATCH", "ANT", "signal", "L1", h, h + 35e-6, [rect(-pw / 2, -pl / 2, pw / 2, pl / 2)]
    )
    feed_x = -6e-3
    fw = 1.0e-3
    port = EMLumpedPort(
        name="P1",
        positive=EMTerminalGeom(position=(feed_x, 0.0), z=h, layer="L1", extent=(0.0, fw)),
        negative=EMTerminalGeom(position=(feed_x, 0.0), z=0.0, layer="L2", extent=(0.0, fw)),
        width=fw,
        z0=50.0,
    )
    st = _structure(box, [mat, CU_MAT], [slab], [gnd, patch])
    bnd = Boundaries(
        xmin="absorbing",
        xmax="absorbing",
        ymin="absorbing",
        ymax="absorbing",
        zmin="absorbing",
        zmax="absorbing",
    )
    return _problem(
        "patch_2p4",
        st,
        [port],
        bnd,
        f_max,
        n_points,
        quality,
        f_min=f_min,
        energy_db=-40.0,
        max_steps=60_000,
    )


# ---------------------------------------------------------------------------------- B-EM-06b
def msl_notch(
    f_max: float = 8.0e9, n_points: int = 160, quality: str = "medium", stub: float = 12.0e-3
) -> EMProblem:
    """Microstrip thru (1.5 mm line on 1.524 mm eps_r 3.66, about 50 ohm) with an open stub of
    length ``stub`` [m] at mid length: a notch near ``c / (4 stub sqrt(eps_eff))``."""
    h, er = 1.524e-3, 3.66
    w = 3.4e-3
    length = 40e-3
    y_half = 14e-3
    air = 10e-3
    mat = DielectricMaterial(name="RO4350", model=ConstantDkDf(dk=er, df=0.0, f_ref=5e9))
    box = Box3(xmin=0.0, xmax=length, ymin=-y_half, ymax=y_half, zmin=0.0, zmax=h + CU + air)
    xs = 0.5 * length
    line = [rect(0.0, -w / 2, length, w / 2), rect(xs - w / 2, w / 2, xs + w / 2, w / 2 + stub)]
    from shapely.geometry import box as sbox
    from shapely.ops import unary_union

    u = unary_union(
        [sbox(0.0, -w / 2, length, w / 2), sbox(xs - w / 2, w / 2, xs + w / 2, w / 2 + stub)]
    )
    ring = [(float(x), float(y)) for x, y in list(u.exterior.coords)[:-1]]
    line = [Polygon(exterior=Points(ring))]
    tr = _body("cb:SIG", "SIG", "signal", "L1", h, h + CU, line)
    st = _structure(
        box,
        [mat, CU_MAT],
        [MaterialSlab(material="RO4350", z_bottom=0.0, z_top=h)],
        [_plane("cb:L2", "L2", -CU, 0.0, box), tr],
    )
    ports = [
        EMWavePort(
            name=n,
            face=f,  # type: ignore[arg-type]
            rect=(-y_half, y_half, box.zmin, box.zmax),
            line_type="microstrip",
            signal_conductors=("cb:SIG",),
        )
        for n, f in (("P1", "xmin"), ("P2", "xmax"))
    ]
    bnd = Boundaries(
        xmin="absorbing",
        xmax="absorbing",
        ymin="absorbing",
        ymax="absorbing",
        zmin="pec",
        zmax="absorbing",
    )
    return _problem("msl_notch", st, ports, bnd, f_max, n_points, quality, energy_db=-40.0)
