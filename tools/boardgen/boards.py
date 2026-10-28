# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Board definitions for WP-102.  Each builder returns a fully populated :class:`kwriter.Board`."""

from __future__ import annotations

import math

import features as F
from kwriter import STACK4, STACK4_PWR, STACK6, STACK8, Board, metres

RULES = [
    "RP-001",
    "RP-002",
    "RP-003",
    "RP-004",
    "RP-007",
    "ED-001",
    "ED-002",
    "DP-001",
    "DP-002",
    "DP-003",
    "RF-001",
    "PD-001",
    "VI-001",
]


def _base(name: str, stk, size, kicad: int = 10) -> Board:
    b = Board(name, stk, size, kicad=kicad)
    b.edge_rect(0, 0, size[0], size[1])
    return b


def microstrip_board(kicad: int = 10, name: str | None = None) -> Board:
    b = _base(name or ("microstrip" if kicad >= 10 else "microstrip_v9"), STACK4, (70, 40), kicad)
    for li in (
        2,
        3,
        4,
    ):  # outline inset 0.5 mm: KiCad 10 DRC measures v9-format zone outlines, not their fill
        F.plane(b, "GND", li, rect=(0.5, 0.5, 69.5, 39.5))
    F.stitch_corners(b)
    F.microstrip(b, 10, 10)
    b.exp["description"] = (
        "4-layer 1.6 mm board. Three 50 mm L1 microstrip traces (0.25/0.35/0.5 mm) over an L2 GND "
        "plane, "
        "h = 0.2 mm, no solder mask. Edge launch pads at both ends."
    )
    if kicad < 10:
        b.exp["notes"].append(
            'KiCad 9 syntax variant: top-level (net N "name") table, numeric item nets, '
            "version 20241229. Copper is identical to `microstrip`."
        )
    return b


def stripline_board() -> Board:
    b = _base("stripline", STACK6, (80, 40))
    F.plane(b, "GND", 2)
    F.plane(b, "GND", 4, rect=(0, 0, 80, 20))  # L4 plane only under the symmetric stripline
    F.plane(b, "GND", 5)
    F.stitch_corners(b)
    F.stripline(b, 15, 10, sym_li=3, off_li=4, off_dy=20)
    b.exp["description"] = (
        "6-layer 1.6 mm board. SL_SYM: symmetric stripline on L3 between L2 and L4 GND (0.2 mm "
        "each side). "
        "SL_OFF: offset stripline on L4 at y=30 mm where L3 is empty above and L5 GND is 0.2 mm "
        "below "
        "(L4 plane exists only for y<20 mm)."
    )
    return b


def diff_pair_board() -> Board:
    b = _base("diff_pair", STACK6, (100, 30))
    for li in (2, 4, 5):
        F.plane(b, "GND", li)
    F.stitch_corners(b)
    F.diff_pair(b, 10, 15)
    b.exp["description"] = (
        "6-layer board. One 100 ohm class edge-coupled pair DP_P/DP_N: L1 microstrip (3 mm "
        "uncoupled breakout, "
        "0.4 mm length mismatch bump), a blind via pair with 4 GND return vias, L3 stripline with "
        "a gap step, "
        "a second via pair with 4 return vias, and L1 pads."
    )
    b.exp["diff_pairs"] = [
        {
            "id": "dp:DP",
            "p_net": "DP_P",
            "n_net": "DP_N",
            "origin": "naming",
            "net_class": "100ohm",
            "target_z_diff": 100.0,
        }
    ]
    return b


def cpwg_board() -> Board:
    b = _base("cpwg", STACK4, (60, 40))
    F.plane(b, "GND", 1, clearance=0.15)
    F.plane(b, "GND", 2)
    F.plane(b, "GND", 4)
    F.stitch_corners(b)
    F.cpwg(b, 10, 10)
    b.exp["description"] = (
        "4-layer board. Two 40 mm L1 CPWG lines (S=0.3 mm, G=0.15 mm, h=0.2 mm) in an L1 GND pour "
        "(zone clearance "
        "0.15 mm) with via fences at 1.6 mm pitch (CPW_A, passes) and 4.8 mm pitch (CPW_B, RF-001)."
    )
    return b


def split_plane_board() -> Board:
    b = _base("split_plane", STACK4, (100, 70))
    F.plane(b, "GND", 2, clearance=0.2)
    F.plane(b, "GND", 3)
    F.plane(b, "GND", 4)
    F.stitch_corners(b)
    F.split_plane(b)
    b.exp["description"] = (
        "4-layer board with deliberate reference-plane defects under L1 traces: 1 mm slot "
        "(RP-001), trace 0.1 mm from "
        "the plane edge (ED-001), trace 0.3 mm from the board edge (ED-002), trace over a void "
        "(RP-007), "
        "swiss-cheese antipad cluster (RP-002) and a GND -> +3V3 reference change (RP-003)."
    )
    b.rules["min_copper_edge_clearance"] = 0.1
    return b


def via_transition_board() -> Board:
    b = _base("via_transition", STACK8, (70, 60))
    for li, net in ((2, "GND"), (4, "GND"), (5, "+1V0"), (7, "GND")):
        F.plane(b, net, li, clearance=0.3)
    F.stitch_corners(b)
    F.net_via(b, 60, 50, "+1V0")
    F.via_transition(b)
    b.exp["description"] = (
        "8-layer 2.0 mm board. SE1: L1->L6 signal over a through via with a 0.62 mm stub (VI-001). "
        "SE2: the same via "
        "backdrilled from the bottom to L7. VD_P/VD_N: through-via pair L1->L8 with 4 return vias. "
        "NR: an L1->L8 "
        "transition without any return via (RP-004). Plus one blind, one buried and one micro via."
    )
    b.exp["diff_pairs"] = [
        {
            "id": "dp:VD",
            "p_net": "VD_P",
            "n_net": "VD_N",
            "origin": "naming",
            "net_class": None,
            "target_z_diff": None,
        }
    ]
    b.exp["notes"].append(
        "KiCad 10.0.5 exports SE2's backdrill to IPC-2581 as Spec BD_1B: START_LAYER B.Cu, "
        "MUST_NOT_CUT_LAYER In5.Cu "
        "(= L6, the last used layer), MAX_STUB_LENGTH 0.1925 mm (observed value). pcbnew reports "
        "the end layer In6.Cu "
        "(L7) as itself cut (IsBackdrilledOrPostMachined true on In6.Cu and B.Cu)."
    )
    return b


def _plane_pair_em(a: float, bb: float, er: float, d: float) -> dict:
    c0 = F.C0
    f10 = c0 / (2 * a * math.sqrt(er))
    f01 = c0 / (2 * bb * math.sqrt(er))
    f11 = c0 / (2 * math.sqrt(er)) * math.sqrt((1 / a) ** 2 + (1 / bb) ** 2)
    return {
        "plane_pair": {
            "a": metres(a * 1e3),
            "b": metres(bb * 1e3),
            "dielectric_thickness": metres(d),
            "dk": er,
            "f10_hz": round(f10, 0),
            "f01_hz": round(f01, 0),
            "f11_hz": round(f11, 0),
            "formula": "f_mn = c / (2 sqrt(Dk)) sqrt((m/a)^2 + (n/b)^2), lossless PMC-wall cavity",
        }
    }


def power_plane_board() -> Board:
    b = _base("power_plane", STACK4_PWR, (60, 50))
    F.plane(b, "GND", 2, clearance=0.2)
    F.plane(b, "+1V0", 3, clearance=0.2, rect=(10, 10, 50, 40))
    F.plane(b, "GND", 4, clearance=0.2)
    F.stitch_corners(b)
    F.power_loads(b, 0, 0, plane_li=3, last_li=4, plane_rect=(10, 10, 50, 40))
    b.exp["description"] = (
        "4-layer board with a thin L2-L3 core (0.1 mm). L2 GND, L3 +1V0 plane 40 x 30 mm, L4 GND. "
        "VRM U5 (3 output "
        "pins) at the left, load U1 (16 +1V0 + 16 GND pins on a 1 mm grid, via-in-pad) at the "
        "right, decap C1 1.5 mm "
        "and C2 12 mm from the nearest U1 power pin."
    )
    b.exp["em"] = {
        "frequency_range_hz": [1e7, 3e9],
        **_plane_pair_em(0.04, 0.03, 4.3, 0.1e-3),
        "ports": [
            {"name": "vrm", "positive": "U5 pins 1-3 (+1V0)", "negative": "U5 pins 4-6 (GND)"},
            {"name": "load", "positive": "U1 +1V0 pins", "negative": "U1 GND pins"},
        ],
    }
    return b


def power_strip_board() -> Board:
    b = _base("power_strip", STACK4, (70, 20))
    L, W = 50.0, 5.0
    b.zone("STRIP", 1, [(10, 7.5), (10 + L, 7.5), (10 + L, 7.5 + W), (10, 7.5 + W)], clearance=0.3)
    b.footprint(
        "J1", 9.5, 10, [{"n": 1, "type": "smd", "shape": "rect", "w": 1.0, "h": W, "net": "STRIP"}]
    )
    b.footprint(
        "J2", 60.5, 10, [{"n": 1, "type": "smd", "shape": "rect", "w": 1.0, "h": W, "net": "STRIP"}]
    )
    t = STACK4.cu_t[0] * 1e-3
    rs = 1 / (F.SIGMA_CU * t)
    r = rs * (L / W)
    b.exp["geometry"]["power_strip"] = {
        "net": "STRIP",
        "layer": "L1",
        "length": metres(L),
        "width": metres(W),
        "copper_thickness": t,
        "strip_mm": [10, 7.5, 10 + L, 7.5 + W],
        "end_pads": [
            {"ref": "J1", "pin": "1", "size_mm": [1.0, W]},
            {"ref": "J2", "pin": "1", "size_mm": [1.0, W]},
        ],
        "pad_placement": (
            "full-width pads abut the strip ends; L is the strip between the pads' inner edges"
        ),
    }
    b.exp["dc"] = {
        "cases": [
            {
                "name": "strip_end_to_end",
                "terminals": [["J1", "1"], ["J2", "1"]],
                "sheet_resistance_ohm_sq": rs,
                "resistance_ohm": r,
                "tolerance_rel": 0.005,
                "formula": (
                    "R = Rs * L / W, Rs = 1 / (sigma * t), sigma = 5.8e7 S/m, t = 35 um, L = 50 "
                    "mm, W = 5 mm"
                ),
            }
        ]
    }
    b.exp["description"] = (
        "Analytic DC resistance variant: a single 50 x 5 mm L1 copper strip with full-width end "
        "pads "
        "J1/J2 on a 4-layer board; R = Rs L / W = 4.926 mOhm."
    )
    return b


def power_two_layer_vias_board(n_side: int = 3) -> Board:
    b = _base("power_two_layer_vias", STACK4, (40, 40))
    b.zone("VX", 1, [(10, 10), (30, 10), (30, 30), (10, 30)], clearance=0.3)
    b.zone("VX", 4, [(10, 10), (30, 10), (30, 30), (10, 30)], clearance=0.3)
    drill, size, pitch = 0.3, 0.6, 2.0
    for i in range(n_side):
        for j in range(n_side):
            b.via(20 + (i - 1) * pitch, 20 + (j - 1) * pitch, "VX", size, drill)
    b.footprint(
        "J1",
        20,
        20,
        [{"n": 1, "type": "smd", "shape": "rect", "w": 6.0, "h": 6.0, "net": "VX"}],
        side=1,
    )
    b.footprint(
        "J2",
        20,
        20,
        [{"n": 1, "type": "smd", "shape": "rect", "w": 6.0, "h": 6.0, "net": "VX"}],
        side=-1,
    )
    n = n_side * n_side
    tp = 25e-6
    d = drill * 1e-3
    area = math.pi / 4 * (d**2 - (d - 2 * tp) ** 2)
    length = STACK4.total_nm() * 1e-9
    r1 = length / (F.SIGMA_CU * area)
    b.exp["geometry"]["power_two_layer_vias"] = {
        "net": "VX",
        "layers": ["L1", "L4"],
        "square_size": metres(20.0),
        "squares_mm": [10, 10, 30, 30],
        "via_count": n,
        "via_size": metres(size),
        "via_drill": metres(drill),
        "via_pitch": metres(pitch),
        "via_grid_centre_mm": [20, 20],
        "plating_thickness_default": tp,
        "terminal_pads": [
            {"ref": "J1", "layer": "L1", "size_mm": [6.0, 6.0]},
            {"ref": "J2", "layer": "L4", "size_mm": [6.0, 6.0]},
        ],
        "terminal_note": (
            "6 x 6 mm pads cover the whole via array, so no sheet spreading resistance enters R"
        ),
    }
    b.exp["dc"] = {
        "cases": [
            {
                "name": "via_array_pad_to_pad",
                "terminals": [["J1", "1"], ["J2", "1"]],
                "via_barrel_resistance_ohm": r1,
                "resistance_ohm": r1 / n,
                "tolerance_rel": 0.005,
                "barrel_length": length,
                "barrel_area": area,
                "formula": "R_via = L / (sigma * A), A = pi/4 (d^2 - (d - 2 tp)^2), d = drill 0.3 "
                "mm (KiCad drill is the hole "
                "to be plated), tp = 25 um (model default; KiCad stores no plating thickness), "
                "L = z_top(L1) - z_bottom(L4) = 1.6 mm, sigma = 5.8e7 S/m; R = R_via / 9",
            }
        ]
    }
    b.exp["description"] = (
        "Analytic DC variant: two 20 x 20 mm squares (L1 and L4, net VX) joined by 9 vias (3 x 3, "
        "2 mm pitch); 6 x 6 mm terminal pads J1 (L1) and J2 (L4) over the array."
    )
    return b


def usb_pair(
    b: Board, x0: float, yc: float, p: str = "USB_D+", n: str = "USB_D-", length: float = 20.0
) -> None:
    w, gap = 0.2, 0.15
    half = (w + gap) / 2
    d = 0.5 - half
    for net, s in ((p, -1), (n, 1)):
        pts = [
            (x0, yc + s * 0.5),
            (x0 + 1, yc + s * 0.5),
            (x0 + 1 + d, yc + s * half),
            (x0 + length - 1 - d, yc + s * half),
            (x0 + length - 1, yc + s * 0.5),
            (x0 + length, yc + s * 0.5),
        ]
        b.track(1, net, w, pts)
        b.tp(x0, yc + s * 0.5, net, 1, 0.6)
        b.tp(x0 + length, yc + s * 0.5, net, 1, 0.6)
    b.netclasses.append(
        {
            "name": "90ohm",
            "track_width": w,
            "clearance": 0.1,
            "diff_pair_width": w,
            "diff_pair_gap": gap,
        }
    )
    b.patterns.append(("90ohm", "USB_D*"))
    b.exp["geometry"]["usb_pair"] = {
        "p_net": p,
        "n_net": n,
        "layer": "L1",
        "width": metres(w),
        "gap": metres(gap),
        "length": metres(length),
        "naming": "Altium-style explicit +/- suffix (USB_D+ / USB_D-)",
        "diff_target_ohm": 90.0,
        "x_range_mm": [x0, x0 + length],
        "y_mm": yc,
    }


def combined_board() -> Board:
    W, H = 240, 140
    b = _base("combined", STACK8, (W, H))
    for li, net, cl in ((2, "GND", 0.2), (4, "GND", 0.3), (5, "+1V0", 0.3), (7, "GND", 0.3)):
        F.plane(b, net, li, clearance=cl)
    F.stitch_corners(b)
    F.net_via(b, W - 10, 10, "+1V0")
    F.split_plane(b, 0, 0)  # 100 x 70 block at top-left
    F.microstrip(b, 110, 10, classes=True)
    F.cpwg(b, 175, 10)
    b.patterns.append(("50ohm", "CPW_*"))
    usb_pair(b, 110, 50)
    F.via_transition(b, 0, 75)
    F.diff_pair(b, 80, 100)
    F.power_loads(b, 170, 75, plane_li=5, last_li=8, plane_rect=(0, 0, W, H))
    b.exp["description"] = (
        "8-layer 2.0 mm board carrying every violation of the other boards in separate regions, "
        "plus net classes "
        "50ohm / 90ohm / 100ohm and the USB_D+ / USB_D- pair. L2/L4/L7 GND planes, L5 +1V0 plane "
        "(full board)."
    )
    b.exp["diff_pairs"] = [
        {
            "id": "dp:DP",
            "p_net": "DP_P",
            "n_net": "DP_N",
            "origin": "naming",
            "net_class": "100ohm",
            "target_z_diff": 100.0,
        },
        {
            "id": "dp:USB_D",
            "p_net": "USB_D+",
            "n_net": "USB_D-",
            "origin": "naming",
            "net_class": "90ohm",
            "target_z_diff": 90.0,
        },
        {
            "id": "dp:VD",
            "p_net": "VD_P",
            "n_net": "VD_N",
            "origin": "naming",
            "net_class": None,
            "target_z_diff": None,
        },
    ]
    b.exp["notes"].append(
        "KiCad has no explicit diff-pair object: the Altium-style explicit pair is represented by "
        "the +/- naming "
        "and the 90ohm netclass pattern USB_D*."
    )
    b.exp["geometry"]["power_plane"]["note"] = (
        "plane_outline is the whole board in this variant (L5 is a full plane)"
    )
    return b


BOARDS = {
    "microstrip": lambda: microstrip_board(10),
    "microstrip_v9": lambda: microstrip_board(9),
    "stripline": stripline_board,
    "diff_pair": diff_pair_board,
    "cpwg": cpwg_board,
    "split_plane": split_plane_board,
    "via_transition": via_transition_board,
    "power_plane": power_plane_board,
    "power_strip": power_strip_board,
    "power_two_layer_vias": power_two_layer_vias_board,
    "combined": combined_board,
}
