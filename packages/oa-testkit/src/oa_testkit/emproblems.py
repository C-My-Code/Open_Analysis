# SPDX-License-Identifier: GPL-3.0-or-later
"""``EMProblem`` fixtures (interfaces.md section 5).

Hand-built, solver-neutral problems derived from the board builders so that geometry, stackup and
net names agree with the board fixtures (the real flow, ``oa_geometry.cut_region``, is not
needed to test adapters):

- :func:`microstrip_thru_problem`: 25 mm microstrip, 2 lumped ports.
- :func:`stripline_thru_problem`: 25 mm stripline, 2 wave ports (``line_type="stripline"``).
- :func:`via_transition_problem`: L1 -> L4 through-via transition, 2 wave ports.

Conventions: the region box spans the trace in x (``0 .. length``), ``+-y_half`` in y and the
whole stack in z (plus ``air`` above and below where fields extend into air). Wave ports use the
full boundary face. Materials: dispersive FR-4 (Djordjevic-Sarkar) and copper.
"""

from __future__ import annotations

from collections.abc import Sequence

from oa_model.board import Board
from oa_model.em import (
    Boundaries,
    Box3,
    ConductorBody,
    EMLumpedPort,
    EMPort,
    EMProblem,
    EMTerminalGeom,
    EMWavePort,
    FrequencyPlan,
    MaterialSlab,
    RegionProvenance,
    Structure3D,
    ViaBody,
)
from oa_model.geometry import Polygon
from oa_model.materials import ConductorMaterial, DielectricMaterial, DjordjevicSarkar
from oa_model.arrays import Points
from oa_model.stackup import ConductorLayer, DielectricLayer

from . import builders as bld

__all__ = ["microstrip_thru_problem", "stripline_thru_problem", "via_transition_problem"]


def _layer(board: Board, layer_id: str) -> ConductorLayer | DielectricLayer:
    for lyr in board.stackup.layers:
        if lyr.id == layer_id and isinstance(lyr, (ConductorLayer, DielectricLayer)):
            return lyr
    raise KeyError(layer_id)


def _materials(board: Board) -> tuple[DielectricMaterial | ConductorMaterial, ...]:
    """Board materials with the constant-Dk FR-4 upgraded to causal Djordjevic-Sarkar."""
    out: list[DielectricMaterial | ConductorMaterial] = []
    for m in board.stackup.materials:
        if isinstance(m, DielectricMaterial) and m.model.kind == "constant":
            ds = DjordjevicSarkar(dk=m.model.dk, df=m.model.df, f_ref=m.model.f_ref)
            m = m.model_copy(update={"model": ds})
        out.append(m)
    return tuple(out)


def _rect(x0: float, y0: float, x1: float, y1: float) -> Polygon:
    return Polygon(exterior=Points(bld.rect_ring(x0, y0, x1, y1)))


def _slabs(board: Board) -> tuple[MaterialSlab, ...]:
    return tuple(
        MaterialSlab(material=lyr.material, z_bottom=lyr.z_bottom, z_top=lyr.z_top)
        for lyr in board.stackup.layers
        if isinstance(lyr, DielectricLayer)
    )


def _provenance(board: Board, nets: Sequence[str], bbox: Box3) -> RegionProvenance:
    return RegionProvenance(board_hash=board.content_hash(), nets=tuple(nets), bbox=bbox)


def _track_body(board: Board, track_id: str, role: str = "signal") -> ConductorBody:
    trk = next(t for t in board.tracks if t.id == track_id)
    lyr = _layer(board, trk.layer)
    seg = trk.path[0]
    x0, x1 = sorted((seg.start[0], seg.end[0]))
    hw = trk.width / 2.0
    y = seg.start[1]
    return ConductorBody(
        id=f"cb:{trk.layer}:{trk.net}",
        net=trk.net,
        role=role,  # type: ignore[arg-type]
        layer=trk.layer,
        z_bottom=lyr.z_bottom,
        z_top=lyr.z_top,
        polygons=(_rect(x0, y - hw, x1, y + hw),),
    )


def _plane_body(
    board: Board, layer_id: str, box: Box3, holes: Sequence[tuple[float, float, float]] = ()
) -> ConductorBody:
    lyr = _layer(board, layer_id)
    ring = bld.rect_ring(box.xmin, box.ymin, box.xmax, box.ymax)
    poly = Polygon(
        exterior=Points(ring),
        holes=tuple(Points(bld.circle_ring(cx, cy, r, 32, clockwise=True)) for cx, cy, r in holes),
    )
    return ConductorBody(
        id=f"cb:{layer_id}:GND",
        net="GND",
        role="reference",
        layer=layer_id,
        z_bottom=lyr.z_bottom,
        z_top=lyr.z_top,
        polygons=(poly,),
    )


def _freq(f_max: float, n_points: int) -> FrequencyPlan:
    return FrequencyPlan(f_min=0.0, f_max=f_max, n_points=n_points)


def microstrip_thru_problem(
    *,
    length: float = 25e-3,
    width: float = 0.38e-3,
    h: float = 0.2e-3,
    y_half: float = 2.5e-3,
    air: float = 1.0e-3,
    f_max: float = 10e9,
    n_points: int = 201,
) -> EMProblem:
    """25 mm microstrip thru with two lumped ports at the trace ends.

    Each port connects the trace end (L1 bottom face level) to the L2 plane top, current sheet
    width = trace width. Open sides absorb; the bottom of the box is the L2 plane (PEC).
    """
    board = bld.microstrip_board(length=length, width=width, h=h)
    l1, l2 = _layer(board, "L1"), _layer(board, "L2")
    box = Box3(
        xmin=0.0, xmax=length, ymin=-y_half, ymax=y_half, zmin=0.0, zmax=board.stackup.total_thickness + air
    )
    trace = _track_body(board, "trk:SIG:1")
    ports: list[EMPort] = []
    for name, x in (("P1", 0.0), ("P2", length)):
        ports.append(
            EMLumpedPort(
                name=name,
                positive=EMTerminalGeom(position=(x, 0.0), z=l1.z_bottom, layer="L1", extent=(0.0, width)),
                negative=EMTerminalGeom(position=(x, 0.0), z=l2.z_top, layer="L2", extent=(0.0, width)),
                width=width,
                z0=50.0,
                excite=True,
            )
        )
    structure = Structure3D(
        bbox=box,
        materials=_materials(board),
        slabs=_slabs(board),
        conductors=(trace, _plane_body(board, "L2", box)),
        vias=(),
        provenance=_provenance(board, ["SIG", "GND"], box),
    )
    return EMProblem(
        name="microstrip_thru_25mm_lumped",
        structure=structure,
        ports=tuple(ports),
        boundaries=Boundaries(
            xmin="absorbing", xmax="absorbing", ymin="absorbing", ymax="absorbing",
            zmin="pec", zmax="absorbing",
        ),
        frequency=_freq(f_max, n_points),
    )


def stripline_thru_problem(
    *,
    length: float = 25e-3,
    width: float = 0.2e-3,
    h1: float = 0.2e-3,
    h2: float = 0.2e-3,
    y_half: float = 2.5e-3,
    f_max: float = 10e9,
    n_points: int = 201,
) -> EMProblem:
    """25 mm stripline thru, two wave ports on the xmin/xmax faces (full face).

    The box is closed by the L1/L3 planes (zmin/zmax PEC) and PEC side walls at +-y_half.
    """
    board = bld.stripline_board(length=length, width=width, h1=h1, h2=h2)
    box = Box3(
        xmin=0.0, xmax=length, ymin=-y_half, ymax=y_half, zmin=0.0, zmax=board.stackup.total_thickness
    )
    trace = _track_body(board, "trk:SIG:1")
    structure = Structure3D(
        bbox=box,
        materials=_materials(board),
        slabs=_slabs(board),
        conductors=(_plane_body(board, "L1", box), trace, _plane_body(board, "L3", box)),
        vias=(),
        provenance=_provenance(board, ["SIG", "GND"], box),
    )
    ports = tuple(
        EMWavePort(
            name=name,
            face=face,  # type: ignore[arg-type]
            rect=(box.ymin, box.ymax, box.zmin, box.zmax),
            n_modes=1,
            line_type="stripline",
            signal_conductors=(trace.id,),
            z0=None,
            excite=True,
        )
        for name, face in (("P1", "xmin"), ("P2", "xmax"))
    )
    return EMProblem(
        name="stripline_thru_25mm_wave",
        structure=structure,
        ports=ports,
        boundaries=Boundaries(
            xmin="absorbing", xmax="absorbing", ymin="pec", ymax="pec", zmin="pec", zmax="pec"
        ),
        frequency=_freq(f_max, n_points),
    )


def via_transition_problem(
    *,
    length: float = 25e-3,
    width: float = 0.38e-3,
    y_half: float = 3.0e-3,
    air: float = 1.0e-3,
    f_max: float = 20e9,
    n_points: int = 201,
) -> EMProblem:
    """Single signal-via transition L1 -> L4 with two microstrip wave ports (xmin: L1, xmax: L4).

    The via drill/plating come from the board's ``via_sig`` padstack; the four return vias are
    included as reference bodies. Planes L2/L3 carry the antipad holes.
    """
    board = bld.via_transition_board(length=length, width=width)
    total = board.stackup.total_thickness
    box = Box3(xmin=0.0, xmax=length, ymin=-y_half, ymax=y_half, zmin=-air, zmax=total + air)
    ps = {p.id: p for p in board.padstacks}
    via_bodies: list[ViaBody] = []
    antipad_r = 0.0
    sig_xy = (0.0, 0.0)
    for v in board.vias:
        drill = ps[v.padstack].drill
        assert drill is not None
        r_outer = drill.diameter / 2.0
        s, e = _layer(board, v.start_layer), _layer(board, v.end_layer)
        is_sig = v.net == "SIG"
        if is_sig:
            sig_xy = v.position
            antipad_r = max(
                (getattr(ly.antipad, "diameter", 0.0) for ly in ps[v.padstack].layers), default=0.0
            ) / 2.0
        via_bodies.append(
            ViaBody(
                id=f"vb:{v.id}",
                net=v.net,
                role="signal" if is_sig else "reference",
                center=v.position,
                z_bottom=e.z_bottom,
                z_top=s.z_top,
                r_outer=r_outer,
                plating=drill.plating_thickness,
            )
        )
    holes = [(sig_xy[0], sig_xy[1], antipad_r)]
    t1 = _track_body(board, "trk:SIG:L1")
    t4 = _track_body(board, "trk:SIG:L4")
    structure = Structure3D(
        bbox=box,
        materials=_materials(board),
        slabs=_slabs(board),
        conductors=(
            t1,
            _plane_body(board, "L2", box, holes),
            _plane_body(board, "L3", box, holes),
            t4,
        ),
        vias=tuple(via_bodies),
        provenance=_provenance(board, ["SIG", "GND"], box),
    )
    rect = (box.ymin, box.ymax, box.zmin, box.zmax)
    ports = (
        EMWavePort(
            name="P1", face="xmin", rect=rect, line_type="microstrip",
            signal_conductors=(t1.id,), z0=None, excite=True,
        ),
        EMWavePort(
            name="P2", face="xmax", rect=rect, line_type="microstrip",
            signal_conductors=(t4.id,), z0=None, excite=True,
        ),
    )
    return EMProblem(
        name="via_transition_L1_L4_wave",
        structure=structure,
        ports=ports,
        boundaries=Boundaries(
            xmin="absorbing", xmax="absorbing", ymin="absorbing", ymax="absorbing",
            zmin="absorbing", zmax="absorbing",
        ),
        frequency=_freq(f_max, n_points),
    )
