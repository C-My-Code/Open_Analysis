# SPDX-License-Identifier: GPL-3.0-or-later
"""Inline board builders for the oa_model tests (the shared builders live in oa_testkit)."""

from __future__ import annotations

import math

from oa_model import (
    ArcSeg,
    Board,
    BoardMeta,
    BoardOutline,
    Component,
    ConductorLayer,
    ConductorMaterial,
    ConstantDkDf,
    CopperShape,
    DielectricLayer,
    DielectricMaterial,
    DiffPair,
    Drill,
    LineSeg,
    MaskLayer,
    Net,
    NetClass,
    Pad,
    Padstack,
    PadstackLayer,
    Points,
    Polygon,
    Stackup,
    Track,
    Via,
    Zone,
    ZoneFill,
)
from oa_model.padstack import CircleShape, RectShape
from oa_model.stackup import StackLayer

UM = 1e-6
MM = 1e-3


def rect(x0: float, y0: float, x1: float, y1: float) -> Polygon:
    return Polygon(exterior=Points([x0, y0, x1, y0, x1, y1, x0, y1]))


def circle_ring(cx: float, cy: float, r: float, n: int = 100, ccw: bool = True) -> Points:
    pts = [
        (cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n))
        for k in range(n)
    ]
    return Points(pts if ccw else pts[::-1])


def make_stackup() -> Stackup:
    spec: list[tuple[str, str, float]] = [
        ("MT", "mask", 20 * UM),
        ("L1", "cu", 35 * UM),
        ("D1", "diel", 200 * UM),
        ("L2", "cu", 35 * UM),
        ("D2", "diel", 800 * UM),
        ("L3", "cu", 35 * UM),
        ("D3", "diel", 200 * UM),
        ("L4", "cu", 35 * UM),
        ("MB", "mask", 20 * UM),
    ]
    z = sum(t for *_, t in spec)
    total = z
    layers: list[StackLayer] = []
    cu_names = {"L1": "F.Cu", "L2": "In1.Cu", "L3": "In2.Cu", "L4": "B.Cu"}
    for lid, kind, t in spec:
        top, bottom = z, z - t
        z = bottom
        if kind == "cu":
            layers.append(
                ConductorLayer(
                    id=lid,
                    index=int(lid[1]),
                    source_name=cu_names[lid],
                    thickness=t,
                    z_bottom=bottom,
                    z_top=top,
                )
            )
        elif kind == "diel":
            layers.append(
                DielectricLayer(
                    id=lid,
                    index=int(lid[1]),
                    thickness=t,
                    z_bottom=bottom,
                    z_top=top,
                    material="FR4",
                    construction="core" if lid == "D2" else "prepreg",
                )
            )
        else:
            layers.append(
                MaskLayer(id=lid, thickness=t, z_bottom=bottom, z_top=top, material="mask")  # type: ignore[arg-type]
            )
    mats = (
        ConductorMaterial(name="copper"),
        DielectricMaterial(name="FR4", model=ConstantDkDf(dk=4.3, df=0.02)),
        DielectricMaterial(name="mask", model=ConstantDkDf(dk=3.5, df=0.025)),
    )
    return Stackup(layers=tuple(layers), materials=mats, total_thickness=total)


def make_board(name: str = "demo") -> Board:
    """A 4-layer board with a diff pair, a via, a zone with a hole and a component."""
    smd = Padstack(
        id="ps_smd",
        drill=None,
        layers=(PadstackLayer(layer="L1", pad=RectShape(width=0.6 * MM, height=0.5 * MM)),),
    )
    via_ps = Padstack(
        id="ps_via",
        drill=Drill(kind="plated", diameter=0.3 * MM),
        layers=tuple(
            PadstackLayer(layer=f"L{i}", pad=CircleShape(diameter=0.6 * MM)) for i in range(1, 5)
        ),
    )
    track_p = Track(
        id="t1",
        net="SIG_P",
        layer="L1",
        width=0.15 * MM,
        path=(
            LineSeg(start=(2 * MM, 1 * MM), end=(8 * MM, 1 * MM)),
            ArcSeg(start=(8 * MM, 1 * MM), end=(9 * MM, 2 * MM), center=(8 * MM, 2 * MM), ccw=True),
        ),
    )
    track_n = Track(
        id="t2",
        net="SIG_N",
        layer="L1",
        width=0.15 * MM,
        path=(LineSeg(start=(2 * MM, 1.3 * MM), end=(8 * MM, 1.3 * MM)),),
    )
    zone = Zone(
        id="z1",
        net="GND",
        layers=("L2",),
        outline=rect(0, 0, 20 * MM, 10 * MM),
        fills=(
            ZoneFill(
                layer="L2",
                polygons=(
                    Polygon(
                        exterior=Points([0, 0, 20 * MM, 0, 20 * MM, 10 * MM, 0, 10 * MM]),
                        holes=(circle_ring(10 * MM, 5 * MM, 0.5 * MM, 40, ccw=False),),
                    ),
                ),
            ),
        ),
    )
    return Board(
        meta=BoardMeta(
            name=name,
            source_tool="synthetic",
            reader="oa-model-tests 0",
            created_utc="2026-10-27T00:00:00Z",
        ),
        outline=BoardOutline(polygon=rect(0, 0, 20 * MM, 10 * MM)),
        stackup=make_stackup(),
        nets=(
            Net(id="GND", kind="ground"),
            Net(id="SIG_P", net_class="USB_90ohm"),
            Net(id="SIG_N", net_class="USB_90ohm"),
            Net(id="+3V3", kind="power"),
        ),
        net_classes=(NetClass(name="USB_90ohm", track_width=0.15 * MM),),
        diff_pairs=(DiffPair(id="dp:SIG", p_net="SIG_P", n_net="SIG_N", origin="naming"),),
        padstacks=(smd, via_ps),
        pads=(
            Pad(
                id="p1",
                component="U1",
                number="1",
                padstack="ps_smd",
                position=(2 * MM, 1 * MM),
                rotation=0.0,
                net="SIG_P",
                kind="smd",
            ),
            Pad(
                id="p2",
                component="U1",
                number="2",
                padstack="ps_smd",
                position=(2 * MM, 1.3 * MM),
                rotation=0.0,
                net="SIG_N",
                kind="smd",
            ),
        ),
        vias=(
            Via(
                id="v1",
                padstack="ps_via",
                position=(5 * MM, 5 * MM),
                net="GND",
                kind="through",
                start_layer="L1",
                end_layer="L4",
            ),
        ),
        tracks=(track_p, track_n),
        zones=(zone,),
        shapes=(
            CopperShape(
                id="s1", net="GND", layer="L4", polygon=rect(1 * MM, 1 * MM, 3 * MM, 2 * MM)
            ),
        ),
        components=(
            Component(
                refdes="U1",
                side="top",
                position=(2 * MM, 1 * MM),
                rotation=0.0,
                pads=("p1", "p2"),
                courtyard=rect(1 * MM, 0, 3 * MM, 2 * MM),
            ),
        ),
    )
