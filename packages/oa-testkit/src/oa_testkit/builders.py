# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic ``Board`` builders (board-model.md section 14.5).

Every builder is parameterized with keyword arguments in SI units (metres), is deterministic,
and returns a ``Board`` that passes ``validate_board(strict=True)``. Geometry conventions:

- The signal runs along +X starting at ``x = 0``; the board outline is the trace span plus
  ``margin`` on every side (``margin_x`` is 0 for the edge-launch builders).
- ``z = 0`` is the bottom of the bottom copper layer (no solder mask is modelled).
- Materials are the board-model "default" kind; the physics sidecar overrides them.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal

from oa_model.board import Board, BoardMeta, BoardOutline, Component
from oa_model.common import SourceRef
from oa_model.copper import Track, Zone, ZoneFill
from oa_model.geometry import LineSeg, Polygon
from oa_model.arrays import Points
from oa_model.materials import ConductorMaterial, ConstantDkDf, DielectricMaterial
from oa_model.nets import DiffPair, Net, NetClass
from oa_model.pads import Pad
from oa_model.padstack import CircleShape, Drill, Padstack, PadstackLayer, RectShape
from oa_model.stackup import ConductorLayer, DielectricLayer, Stackup
from oa_model.vias import Via

__all__ = [
    "circle_ring",
    "cpwg_board",
    "diff_pair_board",
    "microstrip_board",
    "power_plane_board",
    "rect_ring",
    "split_plane_board",
    "stripline_board",
    "via_transition_board",
]

READER = "oa-testkit 0.1.0"
CREATED_UTC = "2026-01-01T00:00:00Z"  # fixed so builders are bit-for-bit reproducible
CU_T = 35e-6  # finished copper thickness used by default (about 1 oz)
FR4 = "FR4_generic"
PAD_SIZE = 0.6e-3


# ---------------------------------------------------------------------------------------------
# Geometry helpers (public: the EM fixtures and tests reuse them)
# ---------------------------------------------------------------------------------------------


def rect_ring(x0: float, y0: float, x1: float, y1: float) -> list[tuple[float, float]]:
    """Counter-clockwise rectangle ring (not closed)."""
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def circle_ring(
    cx: float, cy: float, r: float, n: int = 32, *, clockwise: bool = False
) -> list[tuple[float, float]]:
    """Regular n-gon inscribed in the circle; CCW by default, CW for holes."""
    pts = [
        (cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n))
        for i in range(n)
    ]
    return pts[::-1] if clockwise else pts


def _poly(exterior: Sequence[tuple[float, float]], *holes: Sequence[tuple[float, float]]) -> Polygon:
    return Polygon(exterior=Points(exterior), holes=tuple(Points(h) for h in holes))


# ---------------------------------------------------------------------------------------------
# Stackup
# ---------------------------------------------------------------------------------------------


def _materials(er: float, df: float) -> tuple[DielectricMaterial | ConductorMaterial, ...]:
    return (
        DielectricMaterial(name=FR4, model=ConstantDkDf(dk=er, df=df, f_ref=1e9), source="default"),
        ConductorMaterial(name="copper", source="default"),
    )


def make_stackup(
    dielectrics: Sequence[float],
    *,
    cu_t: float | Sequence[float] = CU_T,
    er: float = 4.3,
    df: float = 0.02,
    roles: Sequence[Literal["signal", "plane", "mixed"]] | None = None,
    constructions: Sequence[Literal["core", "prepreg"]] | None = None,
) -> Stackup:
    """Alternating copper/dielectric stack with ``len(dielectrics) + 1`` copper layers.

    ``dielectrics`` lists dielectric thicknesses top to bottom. Layer ids are L1..Ln and D1..Dm
    top to bottom; z is built bottom-up from 0 so the contiguity validator holds exactly.
    """
    n_cu = len(dielectrics) + 1
    cu = [cu_t] * n_cu if isinstance(cu_t, (int, float)) else list(cu_t)
    if len(cu) != n_cu:
        raise ValueError(f"need {n_cu} copper thicknesses, got {len(cu)}")
    roles = list(roles) if roles is not None else ["signal"] * n_cu
    cons = list(constructions) if constructions is not None else ["core"] * len(dielectrics)

    layers: list[ConductorLayer | DielectricLayer] = []
    z = 0.0
    for i in range(n_cu - 1, -1, -1):  # bottom copper first
        z_top = z + cu[i]
        layers.append(
            ConductorLayer(
                id=f"L{i + 1}",
                index=i + 1,
                source_name=f"L{i + 1}",
                role=roles[i],
                thickness=cu[i],
                z_bottom=z,
                z_top=z_top,
            )
        )
        z = z_top
        if i > 0:
            d = dielectrics[i - 1]
            z_top = z + d
            layers.append(
                DielectricLayer(
                    id=f"D{i}",
                    index=i,
                    construction=cons[i - 1],
                    thickness=d,
                    z_bottom=z,
                    z_top=z_top,
                    material=FR4,
                )
            )
            z = z_top
    layers.reverse()
    return Stackup(
        layers=tuple(layers),
        materials=_materials(er, df),
        total_thickness=layers[0].z_top,
        known=True,
    )


# ---------------------------------------------------------------------------------------------
# Common assembly
# ---------------------------------------------------------------------------------------------


def _meta(name: str) -> BoardMeta:
    return BoardMeta(name=name, source_tool="synthetic", reader=READER, created_utc=CREATED_UTC)


def _outline(x0: float, y0: float, x1: float, y1: float) -> BoardOutline:
    return BoardOutline(polygon=_poly(rect_ring(x0, y0, x1, y1)))


def _src(token: str) -> SourceRef:
    return SourceRef(tool="synthetic", token=token)


def _smd_padstack(layer: str = "L1", size: float = PAD_SIZE) -> Padstack:
    return Padstack(
        id=f"smd_{layer}",
        drill=None,
        layers=(PadstackLayer(layer=layer, pad=RectShape(width=size, height=size)),),
    )


def _track(id_: str, net: str, layer: str, width: float, p0: tuple[float, float], p1: tuple[float, float]) -> Track:
    return Track(
        id=id_, net=net, layer=layer, width=width, path=(LineSeg(start=p0, end=p1),), source=_src("segment")
    )


def _pad(refdes: str, number: str, padstack: str, pos: tuple[float, float], net: str) -> Pad:
    return Pad(
        id=f"pad:{refdes}.{number}",
        component=refdes,
        number=number,
        padstack=padstack,
        position=pos,
        rotation=0.0,
        net=net,
        kind="smd",
        source=_src("pad"),
    )


def _component(
    refdes: str, pos: tuple[float, float], pads: Sequence[Pad], side: Literal["top", "bottom"] = "top"
) -> Component:
    return Component(
        refdes=refdes,
        side=side,
        position=pos,
        rotation=0.0,
        pads=tuple(p.id for p in pads),
        footprint="oa_testkit:SMD_PAD",
        source=_src("footprint"),
    )


def _fill_zone(
    id_: str, net: str, layer: str, outline: Polygon, fill: Sequence[Polygon] | None = None
) -> Zone:
    return Zone(
        id=id_,
        net=net,
        layers=(layer,),
        outline=outline,
        fills=(ZoneFill(layer=layer, polygons=tuple(fill) if fill is not None else (outline,)),),
        fill_state="filled",
        source=_src("zone"),
    )


def _via_padstack(
    id_: str,
    layers: Sequence[str],
    *,
    drill: float,
    pad: float,
    antipad_layers: Sequence[str] = (),
    antipad: float | None = None,
    connect_layers: Sequence[str] = (),
) -> Padstack:
    """Plated via padstack with a pad on every layer in ``layers`` except ``antipad_layers``."""
    entries = []
    for lyr in layers:
        if lyr in antipad_layers:
            assert antipad is not None
            entries.append(PadstackLayer(layer=lyr, pad=None, antipad=CircleShape(diameter=antipad)))
        else:
            entries.append(
                PadstackLayer(
                    layer=lyr,
                    pad=CircleShape(diameter=pad),
                    connection="solid" if lyr in connect_layers else "unknown",
                )
            )
    return Padstack(id=id_, drill=Drill(kind="plated", diameter=drill), layers=tuple(entries))


def _assemble(
    name: str,
    *,
    outline: BoardOutline,
    stackup: Stackup,
    nets: Sequence[Net],
    net_classes: Sequence[NetClass] = (),
    diff_pairs: Sequence[DiffPair] = (),
    padstacks: Sequence[Padstack] = (),
    pads: Sequence[Pad] = (),
    vias: Sequence[Via] = (),
    tracks: Sequence[Track] = (),
    zones: Sequence[Zone] = (),
    components: Sequence[Component] = (),
) -> Board:
    return Board(
        meta=_meta(name),
        outline=outline,
        stackup=stackup,
        nets=tuple(nets),
        net_classes=tuple(net_classes),
        diff_pairs=tuple(diff_pairs),
        padstacks=tuple(padstacks),
        pads=tuple(pads),
        vias=tuple(vias),
        tracks=tuple(tracks),
        zones=tuple(zones),
        components=tuple(components),
    )


def _z50() -> NetClass:
    return NetClass(name="Z50", track_width=0.3e-3, target_z_se=50.0, target_tolerance=0.1)


# ---------------------------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------------------------


def microstrip_board(
    *,
    length: float = 25e-3,
    width: float = 0.38e-3,
    h: float = 0.2e-3,
    er: float = 4.3,
    df: float = 0.02,
    cu_t: float = CU_T,
    margin: float = 5e-3,
) -> Board:
    """Surface microstrip thru: L1 trace SIG between connector pads J1.1/J2.1 over a solid L2 GND.

    Defaults give u = W/h = 1.9, about 51 ohm at er = 4.3 (Hammerstad-Jensen).
    """
    stack = make_stackup([h], cu_t=cu_t, er=er, df=df, roles=["signal", "plane"])
    ps = _smd_padstack()
    p1 = _pad("J1", "1", ps.id, (0.0, 0.0), "SIG")
    p2 = _pad("J2", "1", ps.id, (length, 0.0), "SIG")
    board_ring = _poly(rect_ring(-margin, -margin, length + margin, margin))
    return _assemble(
        "oa_testkit_microstrip",
        outline=_outline(-margin, -margin, length + margin, margin),
        stackup=stack,
        nets=[Net(id="SIG", kind="signal", net_class="Z50"), Net(id="GND", kind="ground")],
        net_classes=[_z50()],
        padstacks=[ps],
        pads=[p1, p2],
        tracks=[_track("trk:SIG:1", "SIG", "L1", width, (0.0, 0.0), (length, 0.0))],
        zones=[_fill_zone("zone:GND:L2", "GND", "L2", board_ring)],
        components=[_component("J1", (0.0, 0.0), [p1]), _component("J2", (length, 0.0), [p2])],
    )


def stripline_board(
    *,
    length: float = 25e-3,
    width: float = 0.2e-3,
    h1: float = 0.2e-3,
    h2: float = 0.2e-3,
    er: float = 4.3,
    df: float = 0.02,
    cu_t: float = CU_T,
    margin: float = 5e-3,
) -> Board:
    """Stripline thru: L2 trace SIG between solid GND planes L1 and L3, edge to edge (no pads).

    ``h1``/``h2`` are the dielectric thicknesses above/below the trace; unequal values give an
    offset stripline. Defaults: b = 2h + t = 0.435 mm, W/b = 0.46, about 50 ohm at er = 4.3.
    """
    stack = make_stackup([h1, h2], cu_t=cu_t, er=er, df=df, roles=["plane", "signal", "plane"])
    ring = _poly(rect_ring(0.0, -margin, length, margin))
    return _assemble(
        "oa_testkit_stripline",
        outline=_outline(0.0, -margin, length, margin),
        stackup=stack,
        nets=[Net(id="SIG", kind="signal", net_class="Z50"), Net(id="GND", kind="ground")],
        net_classes=[_z50()],
        tracks=[_track("trk:SIG:1", "SIG", "L2", width, (0.0, 0.0), (length, 0.0))],
        zones=[
            _fill_zone("zone:GND:L1", "GND", "L1", ring),
            _fill_zone("zone:GND:L3", "GND", "L3", ring),
        ],
    )


def diff_pair_board(
    *,
    length: float = 25e-3,
    width: float = 0.15e-3,
    gap: float = 0.15e-3,
    h: float = 0.1e-3,
    er: float = 4.3,
    df: float = 0.02,
    cu_t: float = CU_T,
    margin: float = 5e-3,
) -> Board:
    """Edge-coupled microstrip pair DP_P / DP_N on L1 over a solid L2 GND, pads on J1/J2.

    ``gap`` is the edge-to-edge spacing. The pair is centred on y = 0 with P at +y.
    """
    pitch = (width + gap) / 2.0
    stack = make_stackup([h], cu_t=cu_t, er=er, df=df, roles=["signal", "plane"])
    ps = _smd_padstack(size=max(PAD_SIZE, width))
    pads: list[Pad] = []
    tracks: list[Track] = []
    for net, num, y in (("DP_P", "1", pitch), ("DP_N", "2", -pitch)):
        pads += [_pad("J1", num, ps.id, (0.0, y), net), _pad("J2", num, ps.id, (length, y), net)]
        tracks.append(_track(f"trk:{net}:1", net, "L1", width, (0.0, y), (length, y)))
    my = margin + pitch
    ring = _poly(rect_ring(-margin, -my, length + margin, my))
    return _assemble(
        "oa_testkit_diff_pair",
        outline=_outline(-margin, -my, length + margin, my),
        stackup=stack,
        nets=[
            Net(id="DP_P", kind="signal", net_class="Z90"),
            Net(id="DP_N", kind="signal", net_class="Z90"),
            Net(id="GND", kind="ground"),
        ],
        net_classes=[
            NetClass(name="Z90", dp_width=width, dp_gap=gap, target_z_diff=90.0, target_tolerance=0.1)
        ],
        diff_pairs=[DiffPair(id="dp:DP", p_net="DP_P", n_net="DP_N", origin="naming", target_z_diff=90.0)],
        padstacks=[ps],
        pads=pads,
        tracks=tracks,
        zones=[_fill_zone("zone:GND:L2", "GND", "L2", ring)],
        components=[
            _component("J1", (0.0, 0.0), [p for p in pads if p.component == "J1"]),
            _component("J2", (length, 0.0), [p for p in pads if p.component == "J2"]),
        ],
    )


def cpwg_board(
    *,
    length: float = 25e-3,
    width: float = 0.3e-3,
    gap: float = 0.15e-3,
    h: float = 0.2e-3,
    er: float = 4.3,
    df: float = 0.02,
    cu_t: float = CU_T,
    margin: float = 3e-3,
    via_pitch: float = 2e-3,
    via_offset: float = 0.5e-3,
    via_drill: float = 0.2e-3,
    via_pad: float = 0.4e-3,
) -> Board:
    """Conductor-backed coplanar waveguide: L1 trace SIG with a GND pour on both sides at ``gap``,
    a via fence stitching the pour to the solid L2 GND, edge to edge (no pads).

    ``margin`` is the width of each side pour beyond the gap. The fence sits ``via_offset``
    outside the gap edge, every ``via_pitch`` along x.
    """
    stack = make_stackup([h], cu_t=cu_t, er=er, df=df, roles=["signal", "plane"])
    y_gap = width / 2.0 + gap
    y_out = y_gap + margin
    left = _poly(rect_ring(0.0, y_gap, length, y_out))
    right = _poly(rect_ring(0.0, -y_out, length, -y_gap))
    full = _poly(rect_ring(0.0, -y_out, length, y_out))
    n_fence = int(length // via_pitch)
    x_start = (length - (n_fence - 1) * via_pitch) / 2.0
    y_via = y_gap + via_offset
    ps = _via_padstack("via_gnd", ["L1", "L2"], drill=via_drill, pad=via_pad, connect_layers=["L1", "L2"])
    vias = [
        Via(
            id=f"via:GND:{side}{i}",
            padstack=ps.id,
            position=(x_start + i * via_pitch, sy * y_via),
            net="GND",
            kind="through",
            start_layer="L1",
            end_layer="L2",
            source=_src("via"),
        )
        for i in range(n_fence)
        for side, sy in (("p", 1.0), ("n", -1.0))
    ]
    return _assemble(
        "oa_testkit_cpwg",
        outline=_outline(0.0, -y_out, length, y_out),
        stackup=stack,
        nets=[Net(id="SIG", kind="signal", net_class="Z50"), Net(id="GND", kind="ground")],
        net_classes=[_z50()],
        padstacks=[ps],
        vias=vias,
        tracks=[_track("trk:SIG:1", "SIG", "L1", width, (0.0, 0.0), (length, 0.0))],
        zones=[
            _fill_zone("zone:GND:L1", "GND", "L1", full, fill=[left, right]),
            _fill_zone("zone:GND:L2", "GND", "L2", full),
        ],
    )


def split_plane_board(
    *,
    length: float = 25e-3,
    width: float = 0.38e-3,
    h: float = 0.2e-3,
    split_x: float | None = None,
    split_gap: float = 0.5e-3,
    er: float = 4.3,
    df: float = 0.02,
    cu_t: float = CU_T,
    margin: float = 5e-3,
) -> Board:
    """Microstrip crossing a gap in its reference plane.

    L2 is split at ``split_x`` (default: mid-length) into GND (x < split) and VDD (x > split)
    pours separated by ``split_gap``. The L1 trace SIG crosses the slot with no stitching
    capacitor or via, the canonical return-path-discontinuity fixture.
    """
    sx = length / 2.0 if split_x is None else split_x
    stack = make_stackup([h], cu_t=cu_t, er=er, df=df, roles=["signal", "plane"])
    ps = _smd_padstack()
    p1 = _pad("J1", "1", ps.id, (0.0, 0.0), "SIG")
    p2 = _pad("J2", "1", ps.id, (length, 0.0), "SIG")
    x0, x1, y = -margin, length + margin, margin
    left = _poly(rect_ring(x0, -y, sx - split_gap / 2.0, y))
    right = _poly(rect_ring(sx + split_gap / 2.0, -y, x1, y))
    return _assemble(
        "oa_testkit_split_plane",
        outline=_outline(x0, -y, x1, y),
        stackup=stack,
        nets=[
            Net(id="SIG", kind="signal", net_class="Z50"),
            Net(id="GND", kind="ground"),
            Net(id="VDD", kind="power"),
        ],
        net_classes=[_z50()],
        padstacks=[ps],
        pads=[p1, p2],
        tracks=[_track("trk:SIG:1", "SIG", "L1", width, (0.0, 0.0), (length, 0.0))],
        zones=[_fill_zone("zone:GND:L2", "GND", "L2", left), _fill_zone("zone:VDD:L2", "VDD", "L2", right)],
        components=[_component("J1", (0.0, 0.0), [p1]), _component("J2", (length, 0.0), [p2])],
    )


def via_transition_board(
    *,
    length: float = 25e-3,
    width: float = 0.38e-3,
    h1: float = 0.2e-3,
    h_core: float = 0.4e-3,
    h3: float = 0.2e-3,
    er: float = 4.3,
    df: float = 0.02,
    cu_t: float = CU_T,
    margin: float = 5e-3,
    via_drill: float = 0.3e-3,
    via_pad: float = 0.6e-3,
    antipad: float = 1.0e-3,
    n_ground_vias: int = 4,
    ground_via_radius: float = 1.2e-3,
) -> Board:
    """Signal via transition L1 -> L4 through two ground planes (4 layers).

    SIG runs on L1 (microstrip over L2 GND) from J1 to a through via at ``x = length / 2``,
    then on L4 (microstrip under L3 GND) from the via to J2. The signal via has antipads in
    L2/L3 (holes cut in the fills); ``n_ground_vias`` return vias sit on a circle of radius
    ``ground_via_radius`` around it, tying L2 and L3 together.
    """
    stack = make_stackup(
        [h1, h_core, h3], cu_t=cu_t, er=er, df=df, roles=["signal", "plane", "plane", "signal"]
    )
    xv = length / 2.0
    ps = _smd_padstack("L1")
    ps4 = _smd_padstack("L4")
    all_layers = ["L1", "L2", "L3", "L4"]
    sig_via = _via_padstack(
        "via_sig", all_layers, drill=via_drill, pad=via_pad, antipad_layers=["L2", "L3"], antipad=antipad
    )
    gnd_via = _via_padstack(
        "via_gnd", all_layers, drill=via_drill, pad=via_pad, connect_layers=["L2", "L3"]
    )
    p1 = _pad("J1", "1", ps.id, (0.0, 0.0), "SIG")
    p2 = _pad("J2", "1", ps4.id, (length, 0.0), "SIG")
    vias = [
        Via(
            id="via:SIG:1", padstack=sig_via.id, position=(xv, 0.0), net="SIG", kind="through",
            start_layer="L1", end_layer="L4", source=_src("via"),
        )
    ]
    for i in range(n_ground_vias):
        a = 2 * math.pi * (i + 0.5) / n_ground_vias
        vias.append(
            Via(
                id=f"via:GND:{i + 1}",
                padstack=gnd_via.id,
                position=(xv + ground_via_radius * math.cos(a), ground_via_radius * math.sin(a)),
                net="GND",
                kind="through",
                start_layer="L1",
                end_layer="L4",
                source=_src("via"),
            )
        )
    ring = _poly(rect_ring(-margin, -margin, length + margin, margin))
    hole = circle_ring(xv, 0.0, antipad / 2.0, 32, clockwise=True)
    plane_fill = _poly(rect_ring(-margin, -margin, length + margin, margin), hole)
    return _assemble(
        "oa_testkit_via_transition",
        outline=_outline(-margin, -margin, length + margin, margin),
        stackup=stack,
        nets=[Net(id="SIG", kind="signal", net_class="Z50"), Net(id="GND", kind="ground")],
        net_classes=[_z50()],
        padstacks=[ps, ps4, sig_via, gnd_via],
        pads=[p1, p2],
        vias=vias,
        tracks=[
            _track("trk:SIG:L1", "SIG", "L1", width, (0.0, 0.0), (xv, 0.0)),
            _track("trk:SIG:L4", "SIG", "L4", width, (xv, 0.0), (length, 0.0)),
        ],
        zones=[
            _fill_zone("zone:GND:L2", "GND", "L2", ring, fill=[plane_fill]),
            _fill_zone("zone:GND:L3", "GND", "L3", ring, fill=[plane_fill]),
        ],
        components=[
            _component("J1", (0.0, 0.0), [p1]),
            _component("J2", (length, 0.0), [p2], side="bottom"),
        ],
    )


def power_plane_board(
    *,
    size: tuple[float, float] = (40e-3, 30e-3),
    neck_width: float | None = None,
    h: float = 0.2e-3,
    n_decaps: int = 4,
    er: float = 4.3,
    df: float = 0.02,
    cu_t: float = CU_T,
    via_drill: float = 0.2e-3,
    via_pad: float = 0.4e-3,
    antipad: float = 0.8e-3,
) -> Board:
    """Power-delivery fixture (4 layers: L1 parts, L2 GND, L3 VDD plane, L4 spare).

    A VRM component U1 (left) and a load U2 (right) each have a VDD and a GND SMD pad on L1;
    ``n_decaps`` capacitors C1.. sit between them. VDD pads connect to the L3 plane by blind
    vias (L1-L3, antipad in L2), GND pads to L2 by blind vias (L1-L2). The L3 VDD plane is a
    rectangle of ``size``, or a dumbbell whose middle third narrows to ``neck_width`` (a classic
    IR-drop bottleneck). The L2 GND fill carries antipad holes for the VDD vias.
    """
    wx, wy = size
    stack = make_stackup(
        [h, 2 * h, h], cu_t=cu_t, er=er, df=df, roles=["signal", "plane", "plane", "signal"]
    )
    x0, x1 = 0.0, wx
    y0, y1 = -wy / 2.0, wy / 2.0
    if neck_width is None:
        vdd_ring = rect_ring(x0, y0, x1, y1)
    else:
        n = neck_width / 2.0
        xa, xb = wx / 3.0, 2.0 * wx / 3.0
        vdd_ring = [(x0, y0), (xa, y0), (xa, -n), (xb, -n), (xb, y0), (x1, y0),
                    (x1, y1), (xb, y1), (xb, n), (xa, n), (xa, y1), (x0, y1)]
    outline = _poly(rect_ring(x0, y0, x1, y1))
    vdd_poly = _poly(vdd_ring)

    # Placement: parts along the mid line; pad pairs spaced 1.5 mm apart in y.
    sp = 0.75e-3
    parts: list[tuple[str, float]] = [("U1", 0.1 * wx), ("U2", 0.9 * wx)]
    parts += [(f"C{i + 1}", 0.1 * wx + 3e-3 + i * 1.6e-3) for i in range(n_decaps)]
    if parts[-1][1] > wx / 3.0 or (neck_width is not None and neck_width / 2.0 < sp + antipad):
        raise ValueError("n_decaps too large for the plane width, or neck_width too narrow for the vias")

    ps = _smd_padstack(size=0.8e-3)
    layers_l1_l3 = ["L1", "L2", "L3"]
    vdd_via_ps = _via_padstack(
        "via_vdd", layers_l1_l3, drill=via_drill, pad=via_pad, antipad_layers=["L2"], antipad=antipad
    )
    gnd_via_ps = _via_padstack("via_gnd", ["L1", "L2"], drill=via_drill, pad=via_pad, connect_layers=["L2"])

    pads: list[Pad] = []
    comps: list[Component] = []
    vias: list[Via] = []
    holes: list[list[tuple[float, float]]] = []
    for refdes, x in parts:
        yv, yg = sp, -sp
        pv = _pad(refdes, "1", ps.id, (x, yv), "VDD")
        pg = _pad(refdes, "2", ps.id, (x, yg), "GND")
        pads += [pv, pg]
        comps.append(_component(refdes, (x, 0.0), [pv, pg]))
        vias.append(
            Via(id=f"via:VDD:{refdes}", padstack=vdd_via_ps.id, position=(x, yv), net="VDD",
                kind="blind", start_layer="L1", end_layer="L3", source=_src("via"))
        )
        vias.append(
            Via(id=f"via:GND:{refdes}", padstack=gnd_via_ps.id, position=(x, yg), net="GND",
                kind="blind", start_layer="L1", end_layer="L2", source=_src("via"))
        )
        holes.append(circle_ring(x, yv, antipad / 2.0, 24, clockwise=True))
    gnd_fill = _poly(rect_ring(x0, y0, x1, y1), *holes)
    return _assemble(
        "oa_testkit_power_plane",
        outline=_outline(x0, y0, x1, y1),
        stackup=stack,
        nets=[Net(id="VDD", kind="power"), Net(id="GND", kind="ground")],
        padstacks=[ps, vdd_via_ps, gnd_via_ps],
        pads=pads,
        vias=vias,
        zones=[
            _fill_zone("zone:GND:L2", "GND", "L2", outline, fill=[gnd_fill]),
            _fill_zone("zone:VDD:L3", "VDD", "L3", vdd_poly),
        ],
        components=comps,
    )
