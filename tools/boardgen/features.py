# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Feature builders.  Each function draws one test feature at an origin and records ground truth.

Every ERC expectation names the function that created the violating feature (``generator`` key), so
expectations are traceable (WP-102 acceptance).  Units: arguments and drawing in mm (KiCad file frame,
Y down); everything written to ``Board.exp`` is SI except ``location_mm`` / ``tolerance_mm``.
"""

from __future__ import annotations

import math

from kwriter import Board, metres

C0 = 299_792_458.0
SIGMA_CU = 5.8e7  # S/m, annealed copper (IACS)
GEN = "boardgen.features."


# ---------------------------------------------------------------- helpers
def gap_between(b: Board, upper: int, lower: int) -> float:
    """Dielectric+copper distance (mm) from the bottom face of copper layer ``upper`` to the top face of ``lower``."""
    rows = {r["id"]: r for r in b.stk.rows()}
    return round((rows[f"L{upper}"]["z_bottom"] - rows[f"L{lower}"]["z_top"]) * 1e3, 9)


def hj_z0(w: float, h: float, t: float, er: float) -> float:
    """Approximate microstrip Z0, Hammerstad-Jensen with thickness correction (mm in, ohm out)."""
    e = math.e
    coth = lambda x: 1.0 / math.tanh(x)
    dw1 = (t / math.pi) * math.log(1 + 4 * e / ((t / h) * coth(math.sqrt(6.517 * w / t)) ** 2))
    dwr = 0.5 * dw1 * (1 + 1 / math.cosh(math.sqrt(er - 1)))
    u1, ur = (w + dw1) / h, (w + dwr) / h

    def z01(u: float) -> float:
        f = 6 + (2 * math.pi - 6) * math.exp(-((30.666 / u) ** 0.7528))
        return 60 * math.log(f / u + math.sqrt(1 + (2 / u) ** 2))

    a = 1 + math.log((ur ** 4 + (ur / 52) ** 2) / (ur ** 4 + 0.432)) / 49 + math.log(1 + (ur / 18.1) ** 3) / 18.7
    bb = 0.564 * ((er - 0.9) / (er + 3)) ** 0.053
    eeff = (er + 1) / 2 + (er - 1) / 2 * (1 + 10 / ur) ** (-a * bb)
    return z01(ur) / math.sqrt(eeff)


def erc(b: Board, rule: str, nets, li: int, loc, tol: float, gen: str, why: str, **extra) -> None:
    e = {"rule_id": rule, "nets": list(nets), "layer": f"L{li}", "source_layer": b.L(li),
         "location_mm": None if loc is None else [round(loc[0], 6), round(loc[1], 6)],
         "tolerance_mm": tol, "generator": GEN + gen, "why": why}
    e.update(extra)
    b.exp["erc"].append(e)


def negative(b: Board, rule: str, nets, li: int, loc, gen: str, why: str) -> None:
    b.exp["negative_cases"].append({"rule_id": rule, "nets": list(nets), "layer": f"L{li}",
                                    "location_mm": [round(loc[0], 6), round(loc[1], 6)],
                                    "generator": GEN + gen, "why": why})


def plane(b: Board, net: str, li: int, clearance: float = 0.3, rect=None, priority: int = 0) -> None:
    x0, y0, x1, y1 = rect or (0, 0, b.size[0], b.size[1])
    b.zone(net, li, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], priority=priority, clearance=clearance)


def void(b: Board, li: int, x0: float, y0: float, x1: float, y1: float) -> None:
    b.zone("", li, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], keepout_pour=True)


def stitch_corners(b: Board, net: str = "GND", inset: float = 3.0) -> None:
    W, H = b.size
    for x, y in [(inset, inset), (W - inset, inset), (inset, H - inset), (W - inset, H - inset)]:
        b.via(x, y, net, 0.5, 0.25)
        b.tp(x, y, net, 1, 1.0)  # KiCad flags a zone as isolated copper unless a pad of its net connects to it


def net_via(b: Board, x: float, y: float, net: str) -> None:
    """Through via with a top pad, so that the plane of ``net`` is connected to a pad."""
    b.via(x, y, net, 0.5, 0.25)
    b.tp(x, y, net, 1, 1.0)


def end_vias(b: Board, x: float, y: float, net: str, li: int, w: float = 0.8) -> None:
    """Blind via L1 -> ``li`` (no stub) + top pad at an inner-layer trace end."""
    b.via(x, y, net, 0.5, 0.25, span=(1, li), kind="blind")
    b.tp(x, y, net, 1, w)


# ---------------------------------------------------------------- 1. microstrip
def microstrip(b: Board, x0: float, y0: float, widths=(0.25, 0.35, 0.5), length: float = 50.0,
               pitch: float = 10.0, classes: bool = False) -> None:
    """L1 microstrip traces over L2 GND, edge launch pads at both ends."""
    h = gap_between(b, 1, 2)
    t = b.stk.cu_t[0]
    traces = []
    for i, w in enumerate(widths):
        net = f"MS_{round(w * 1000):03d}"
        y = y0 + i * pitch
        b.track(1, net, w, [(x0, y), (x0 + length, y)])
        b.tp(x0, y, net, 1, 1.0, 1.0)
        b.tp(x0 + length, y, net, 1, 1.0, 1.0)
        z0 = hj_z0(w, h, t, 4.3)
        traces.append({"net": net, "layer": "L1", "width": metres(w), "length": metres(length),
                       "start_mm": [x0, y], "end_mm": [x0 + length, y], "z0_ohm_approx": round(z0, 2)})
        dev = abs(z0 - 50) / 50
        if dev > 0.13:
            erc(b, "IM-002", [net], 1, (x0 + length / 2, y), length / 2, "microstrip",
                f"w={w} mm, h={h} mm, Dk=4.3: Z0 ~ {z0:.1f} ohm vs 50 ohm +-10 %")
        elif dev < 0.07:
            negative(b, "IM-002", [net], 1, (x0 + length / 2, y), "microstrip",
                     f"w={w} mm, Z0 ~ {z0:.1f} ohm is within 50 ohm +-10 %")
        else:
            b.exp["unspecified"].append({"rule_id": "IM-002", "nets": [net], "why":
                                         f"Z0 ~ {z0:.1f} ohm is near the +-10 % edge; depends on the solver"})
        if classes:
            pass
    if classes:
        b.netclasses.append({"name": "50ohm", "track_width": 0.35, "clearance": 0.2})
        b.patterns.append(("50ohm", "MS_*"))
    b.exp["geometry"]["microstrip"] = {
        "layer": "L1", "reference_layer": "L2", "h_to_reference": metres(h), "dk": 4.3, "df": 0.02,
        "copper_thickness": metres(t), "length": metres(length), "traces": traces,
        "length_definition": "pad centre to pad centre; the single segment is exactly this long",
        "z0_note": "z0_ohm_approx is Hammerstad-Jensen (no mask); BEM/FEM values differ by about 1 ohm"}


# ---------------------------------------------------------------- 2. stripline
def stripline(b: Board, x0: float, y0: float, sym_li: int, off_li: int, off_dy: float = 20.0,
              length: float = 50.0, w: float = 0.15) -> None:
    """Symmetric stripline on ``sym_li`` (planes either side) and an offset stripline on ``off_li``."""
    for net, li, y in (("SL_SYM", sym_li, y0), ("SL_OFF", off_li, y0 + off_dy)):
        b.track(li, net, w, [(x0, y), (x0 + length, y)])
        end_vias(b, x0, y, net, li)
        end_vias(b, x0 + length, y, net, li)
    g = {
        "width": metres(w), "length": metres(length), "dk": 4.3, "copper_thickness": metres(b.stk.cu_t[0]),
        "symmetric": {"net": "SL_SYM", "layer": f"L{sym_li}", "plane_above": f"L{sym_li - 1}",
                      "plane_below": f"L{sym_li + 1}", "h_above": metres(gap_between(b, sym_li - 1, sym_li)),
                      "h_below": metres(gap_between(b, sym_li, sym_li + 1)),
                      "start_mm": [x0, y0], "end_mm": [x0 + length, y0]},
        "offset": {"net": "SL_OFF", "layer": f"L{off_li}", "plane_above": f"L{sym_li - 1}",
                   "plane_below": f"L{off_li + 1}",
                   "h_above": metres(gap_between(b, sym_li - 1, off_li)),
                   "h_below": metres(gap_between(b, off_li, off_li + 1)),
                   "empty_layer_above": f"L{sym_li}", "start_mm": [x0, y0 + off_dy],
                   "end_mm": [x0 + length, y0 + off_dy]},
        "end_vias": "blind vias 0.5/0.25 mm from L1 to the trace layer with a 0.8 mm top pad at each trace end"}
    b.exp["geometry"]["stripline"] = g


# ---------------------------------------------------------------- 3. differential pair
def diff_pair(b: Board, x0: float, yc: float, p="DP_P", n="DP_N", sl_li: int = 3, classes: bool = True) -> None:
    """100 ohm edge-coupled pair: L1 microstrip -> via pair (+4 GND return vias) -> stripline -> via pair -> L1 pads.

    x0 is the first launch pad x.  The pair ends 75 mm to the right.
    """
    w_ms, gap_ms = 0.15, 0.20
    w_sl, gap_sl, gap_sl2 = 0.12, 0.15, 0.30
    pitch_ms = w_ms + gap_ms
    half = pitch_ms / 2
    brk = 3.0  # uncoupled breakout length
    brk_pitch = 2.0
    via_pitch = 1.2
    bump_h, bump_w = 0.2, 1.0
    xv1 = x0 + 40.0
    xv2 = x0 + 75.0
    lens = {}
    for net, s in ((p, -1), (n, +1)):
        ms = [(x0, yc + s * brk_pitch / 2), (x0 + brk, yc + s * brk_pitch / 2)]
        d = brk_pitch / 2 - half
        ms.append((x0 + brk + d, yc + s * half))
        if s > 0:  # the +y member gets the length-mismatch bump, away from its partner
            xb = x0 + 15.0
            yb = yc + s * half
            ms += [(xb, yb), (xb, yb + bump_h), (xb + bump_w, yb + bump_h), (xb + bump_w, yb)]
        d2 = via_pitch / 2 - half
        ms += [(xv1 - 1.0 - d2, yc + s * half), (xv1 - 1.0, yc + s * via_pitch / 2), (xv1, yc + s * via_pitch / 2)]
        total = b.track(1, net, w_ms, ms)
        # stripline: via, short run, converge, coupled (gap_sl), step to gap_sl2, back, diverge, via
        hs, hs2 = (w_sl + gap_sl) / 2, (w_sl + gap_sl2) / 2
        d3 = via_pitch / 2 - hs
        xs = xv1 + 1.0
        sl = [(xv1, yc + s * via_pitch / 2), (xs, yc + s * via_pitch / 2), (xs + d3, yc + s * hs),
              (xv1 + 20.0, yc + s * hs), (xv1 + 20.0 + (hs2 - hs), yc + s * hs2),
              (xv1 + 25.0, yc + s * hs2), (xv1 + 25.0 + (hs2 - hs), yc + s * hs),
              (xv2 - 1.0 - d3, yc + s * hs), (xv2 - 1.0, yc + s * via_pitch / 2), (xv2, yc + s * via_pitch / 2)]
        total += b.track(sl_li, net, w_sl, sl)
        lens[net] = total
        b.tp(x0, yc + s * brk_pitch / 2, net, 1, 0.8)
    for xv in (xv1, xv2):
        for net, s in ((p, -1), (n, +1)):
            b.via(xv, yc + s * via_pitch / 2, net, 0.5, 0.25, span=(1, sl_li), kind="blind")
        for dx in (-1.5, 1.5):
            for s in (-1, 1):
                b.via(xv + dx, yc + s * 1.9, "GND", 0.5, 0.25)  # 4 return vias per transition
    b.tp(xv2, yc - via_pitch / 2, p, 1, 0.8)
    b.tp(xv2, yc + via_pitch / 2, n, 1, 0.8)
    # ground truth
    mismatch = lens[n] - lens[p]
    if classes:
        b.netclasses.append({"name": "100ohm", "track_width": w_ms, "clearance": 0.1,
                             "diff_pair_width": w_ms, "diff_pair_gap": gap_ms})
        b.patterns.append(("100ohm", "DP_*"))
    b.exp["geometry"]["diff_pair"] = {
        "p_net": p, "n_net": n, "origin": "naming (_P/_N suffix)",
        "microstrip": {"layer": "L1", "width": metres(w_ms), "gap": metres(gap_ms),
                       "x_range_mm": [x0 + brk + (brk_pitch / 2 - half), xv1 - 1.0 - (via_pitch / 2 - half)],
                       "h_to_reference": metres(gap_between(b, 1, 2))},
        "stripline": {"layer": f"L{sl_li}", "width": metres(w_sl), "gap": metres(gap_sl),
                      "gap_step": metres(gap_sl2),
                      "h_above": metres(gap_between(b, sl_li - 1, sl_li)), "h_below": metres(gap_between(b, sl_li, sl_li + 1))},
        "breakout": {"layer": "L1", "uncoupled_length": metres(brk), "pitch": metres(brk_pitch),
                     "x_range_mm": [x0, x0 + brk], "y_mm": yc},
        "gap_variation": {"layer": f"L{sl_li}", "gap_nominal": metres(gap_sl), "gap_wide": metres(gap_sl2),
                          "x_range_mm": [xv1 + 20.0 + (hs2 - hs), xv1 + 25.0], "y_mm": yc},
        "length_p": metres(lens[p]), "length_n": metres(lens[n]), "length_mismatch": metres(mismatch),
        "mismatch_definition": "DP_N minus DP_P: sum of copper segment lengths on L1 and L3 (via barrels excluded); "
                               "DP_N carries a 0.2/1.0/0.2 mm bump (+0.4 mm), everything else is mirror symmetric",
        "via_pairs_mm": [[xv1, yc], [xv2, yc]], "via_kind": "blind", "via_span": ["L1", f"L{sl_li}"], "via_pitch": metres(via_pitch), "return_vias_per_pair": 4,
        "diff_target_ohm": 100.0}
    erc(b, "DP-001", [p, n], 1, (x0 + brk / 2, yc), brk / 2 + 0.5, "diff_pair",
        "pair uncoupled over the 3 mm breakout at the launch end")
    erc(b, "DP-002", [p, n], 1, None, 0.0, "diff_pair",
        f"intra-pair length mismatch {mismatch:.3f} mm caused by the bump at x={x0 + 15.0}..{x0 + 16.0} mm",
        cause_location_mm=[x0 + 15.5, yc + half + bump_h])
    erc(b, "DP-003", [p, n], sl_li, (xv1 + 22.5, yc), 3.0, "diff_pair",
        "gap steps 0.15 -> 0.30 mm for 5 mm on the stripline section")
    negative(b, "RP-004", [p], 1, (xv1, yc), "diff_pair", "layer change has 4 GND return vias")
    b.exp["unspecified"].append({"rule_id": "DP-001", "nets": [p, n], "why":
                                 "the ~1.4 mm fan-outs to the via pairs and the 1 mm stripline fan-outs are also "
                                 "briefly uncoupled; whether they exceed the rule threshold is config dependent"})


# ---------------------------------------------------------------- 4. CPWG
def cpwg(b: Board, x0: float, y0: float, length: float = 40.0, s: float = 0.3, g: float = 0.15,
         pitches=(1.6, 4.8), dy_lines: float = 20.0, fence_off: float = 1.0) -> None:
    """CPWG lines on L1 (GND pour clearance = G) with via fences.  The caller adds the L1 GND zone with clearance G."""
    h = gap_between(b, 1, 2)
    lines = []
    for k, pitch in enumerate(pitches):
        net = f"CPW_{'AB'[k]}"
        y = y0 + k * dy_lines
        b.track(1, net, s, [(x0, y), (x0 + length, y)])
        b.tp(x0, y, net, 1, 0.6)
        b.tp(x0 + length, y, net, 1, 0.6)
        n_half = int((length / 2 - 2.0) // pitch)
        xs = [x0 + length / 2 + i * pitch for i in range(-n_half, n_half + 1)]
        for x in xs:
            for sgn in (-1, 1):
                b.via(x, y + sgn * fence_off, "GND", 0.4, 0.2)
        lines.append({"net": net, "layer": "L1", "start_mm": [x0, y], "end_mm": [x0 + length, y],
                      "fence_pitch": metres(pitch), "fence_offset": metres(fence_off),
                      "fence_via_count": 2 * len(xs), "fence_x_mm": [xs[0], xs[-1]]})
    eeff = 3.3
    lam10 = C0 / (10e9 * math.sqrt(eeff))
    b.exp["geometry"]["cpwg"] = {
        "signal_width_S": metres(s), "gap_G": metres(g), "h_to_reference": metres(h), "dk": 4.3,
        "copper_thickness": metres(b.stk.cu_t[0]), "length": metres(length), "lines": lines,
        "fence_via": {"size": metres(0.4), "drill": metres(0.2), "net": "GND"},
        "rf_check": {"f_max": 10e9, "eeff_nominal": eeff, "lambda_over_10": round(lam10, 9),
                     "pass_pitch": metres(pitches[0]), "fail_pitch": metres(pitches[1]),
                     "formula": "lambda/10 = c / (f * sqrt(eeff)) / 10 with eeff = 3.3 -> 1.651 mm"}}
    ymid = y0 + dy_lines
    erc(b, "RF-001", ["CPW_B"], 1, (x0 + length / 2, ymid), length / 2, "cpwg",
        f"fence pitch {pitches[1]} mm = 3x the lambda/10 pitch at 10 GHz")
    negative(b, "RF-001", ["CPW_A"], 1, (x0 + length / 2, y0), "cpwg",
             f"fence pitch {pitches[0]} mm <= lambda/10 at 10 GHz")


# ---------------------------------------------------------------- 5. split plane
def split_plane(b: Board, x0: float = 0.0, y0: float = 0.0, ref_li: int = 2, swiss_buried=(2, 3)) -> None:
    """Reference-plane defects on layer ``ref_li``; the caller has already added the board-wide GND zone there.

    Local frame (all relative to x0, y0), board footprint 100 x 70 mm.
    """
    X = lambda v: x0 + v  # noqa: E731
    Y = lambda v: y0 + v  # noqa: E731
    # RP-001: 1 mm slot crossed by an L1 trace
    void(b, ref_li, X(49.5), Y(5), X(50.5), Y(45))
    b.track(1, "SIG_SLOT", 0.3, [(X(20), Y(15)), (X(80), Y(15))])
    b.tp(X(20), Y(15), "SIG_SLOT"); b.tp(X(80), Y(15), "SIG_SLOT")
    erc(b, "RP-001", ["SIG_SLOT"], 1, (X(50), Y(15)), 1.0, "split_plane", "L1 trace crosses the 1 mm slot in the L2 GND plane")
    # ED-001: trace edge 0.1 mm from the plane edge (void top edge y=52)
    void(b, ref_li, X(15), Y(52), X(45), Y(60))
    yt = 52 - 0.1 - 0.15
    b.track(1, "SIG_PLEDGE", 0.3, [(X(20), Y(yt)), (X(40), Y(yt))])
    b.tp(X(20), Y(yt), "SIG_PLEDGE", 1, 0.3); b.tp(X(40), Y(yt), "SIG_PLEDGE", 1, 0.3)
    erc(b, "ED-001", ["SIG_PLEDGE"], 1, (X(30), Y(yt)), 10.0, "split_plane",
        "trace edge runs 0.1 mm inboard of the L2 plane edge (void boundary y=52 mm) for 20 mm")
    # RP-007: trace wholly over a region with no L2 copper
    void(b, ref_li, X(60), Y(52), X(90), Y(60))
    b.track(1, "SIG_NOREF", 0.3, [(X(65), Y(56)), (X(85), Y(56))])
    b.tp(X(65), Y(56), "SIG_NOREF"); b.tp(X(85), Y(56), "SIG_NOREF")
    erc(b, "RP-007", ["SIG_NOREF"], 1, (X(75), Y(56)), 10.0, "split_plane", "20 mm trace over a 30 x 8 mm void in L2")
    # RP-002: swiss cheese, 5 x 5 buried vias (pitch 1.6) under a trace
    cx, cy = 20.0, 30.0
    k = 0
    for i in range(-2, 3):
        for j in range(-2, 3):
            k += 1  # one net per via: same-net vias with no copper between them would be DRC "unconnected" errors
            b.via(X(cx + i * 1.6), Y(cy + j * 1.6), f"SC_{k:02d}", 0.6, 0.3, span=swiss_buried, kind="buried")
    b.track(1, "SIG_SWISS", 0.3, [(X(8), Y(cy)), (X(32), Y(cy))])
    b.tp(X(8), Y(cy), "SIG_SWISS"); b.tp(X(32), Y(cy), "SIG_SWISS")
    erc(b, "RP-002", ["SIG_SWISS"], 1, (X(cx), Y(cy)), 4.0, "split_plane",
        "25 buried vias (pitch 1.6 mm, antipad 1.0 mm) perforate the L2 reference under the trace")
    # RP-003: reference changes GND -> +3V3 at x=75 (higher-priority +3V3 zone on the reference layer)
    plane(b, "+3V3", ref_li, clearance=0.2, rect=(X(75), Y(25), X(95), Y(45)), priority=1)
    b.track(1, "SIG_REFCHG", 0.3, [(X(60), Y(35)), (X(90), Y(35))])
    b.tp(X(60), Y(35), "SIG_REFCHG"); b.tp(X(90), Y(35), "SIG_REFCHG")
    net_via(b, X(85), Y(28), "+3V3"); net_via(b, X(90), Y(42), "+3V3")
    erc(b, "RP-003", ["SIG_REFCHG"], 1, (X(75), Y(35)), 1.0, "split_plane",
        "reference net changes GND -> +3V3 at x=75 mm (the 0.2 mm zone clearance gap may also appear as RP-001)")
    b.exp["unspecified"].append({"rule_id": "RP-001", "nets": ["SIG_REFCHG"],
                                 "why": "0.2 mm zone-to-zone clearance gap at the GND/+3V3 boundary"})
    # ED-002: trace edge 0.3 mm from the board edge (needs the board edge at local x=0)
    b.track(1, "SIG_BOARDEDGE", 0.2, [(X(0.4), Y(10)), (X(0.4), Y(40))])
    b.tp(X(0.4), Y(10), "SIG_BOARDEDGE", 1, 0.4); b.tp(X(0.4), Y(40), "SIG_BOARDEDGE", 1, 0.4)
    erc(b, "ED-002", ["SIG_BOARDEDGE"], 1, (X(0.4), Y(25)), 15.0, "split_plane", "trace edge 0.3 mm from the board edge for 30 mm")
    b.exp["geometry"]["split_plane"] = {
        "reference_layer": f"L{ref_li}", "origin_mm": [x0, y0],
        "slot": {"x_range_mm": [X(49.5), X(50.5)], "y_range_mm": [Y(5), Y(45)], "width": metres(1.0),
                 "crossing_trace": "SIG_SLOT", "implemented_as": "copper-pour keepout rule area on the plane layer"},
        "plane_edge_trace": {"net": "SIG_PLEDGE", "edge_to_edge_gap": metres(0.1), "width": metres(0.3),
                             "void_mm": [X(15), Y(52), X(45), Y(60)]},
        "no_reference_void": {"net": "SIG_NOREF", "void_mm": [X(60), Y(52), X(90), Y(60)]},
        "swiss_cheese": {"via_nets": "SC_01..SC_25 (one net per via)", "kind": "buried", "span": [f"L{swiss_buried[0]}", f"L{swiss_buried[1]}"],
                         "count": 25, "pitch": metres(1.6), "via_size": metres(0.6), "drill": metres(0.3),
                         "centre_mm": [X(cx), Y(cy)], "crossing_trace": "SIG_SWISS", "plane_clearance": metres(0.2)},
        "reference_change": {"net": "SIG_REFCHG", "from_net": "GND", "to_net": "+3V3", "x_mm": X(75),
                             "zone_mm": [X(75), Y(25), X(95), Y(45)], "zone_clearance": metres(0.2)},
        "board_edge_trace": {"net": "SIG_BOARDEDGE", "edge_to_edge_gap": metres(0.3), "width": metres(0.2)},
    }


# ---------------------------------------------------------------- 6. via transitions
def via_transition(b: Board, x0: float = 0.0, y0: float = 0.0) -> None:
    """8-layer via structures.  Local frame 70 x 60 mm.  Requires the planes: L2/L4/L7 GND, L5 +1V0."""
    X = lambda v: x0 + v  # noqa: E731
    Y = lambda v: y0 + v  # noqa: E731
    n = b.ncu
    rows = {r["id"]: r for r in b.stk.rows()}
    z_bot = lambda lid: rows[lid]["z_bottom"]  # noqa: E731
    z_top = lambda lid: rows[lid]["z_top"]  # noqa: E731
    w = 0.2
    stub = z_bot("L6") - z_bot(f"L{n}")
    # single-ended, L1 -> L6 over a through via, stub L6 -> L8
    for net, yy, bd in (("SE1", 15.0, None), ("SE2", 35.0, (0.6, n - 1))):
        b.via(X(15), Y(yy), net, 0.6, 0.3, bd_bottom=bd)
        b.track(1, net, w, [(X(7), Y(yy)), (X(15), Y(yy))])
        b.tp(X(7), Y(yy), net)
        b.track(6, net, w, [(X(15), Y(yy)), (X(23), Y(yy))])
        b.tht_tp(X(23), Y(yy), net)
        b.via(X(15), Y(yy + 1.5), "GND", 0.5, 0.25)  # return via 1.5 mm away
    bd_cut = [f"L{n - 1}", f"L{n}"]
    erc(b, "VI-001", ["SE1"], 6, (X(15), Y(15)), 0.5, "via_transition",
        f"through via L1-L{n} with the signal on L6 leaves a {stub * 1e3:.3f} mm stub (L6 bottom to B.Cu)",
        stub_length=metres(stub * 1e3), stub_resonance_hz=round(C0 / (4 * stub * math.sqrt(4.3)), 0))
    b.exp["unspecified"].append({"rule_id": "VI-001", "nets": ["SE2"], "why":
                                 "backdrilled variant; residual stub is threshold dependent"})
    # differential via pair L1 -> L8 (no stub) with four return vias
    for net, yy in (("VD_P", 14.5), ("VD_N", 15.5)):
        b.via(X(40), Y(yy), net, 0.5, 0.25)
        b.track(1, net, 0.15, [(X(32), Y(yy)), (X(40), Y(yy))])
        b.tp(X(32), Y(yy), net)
        b.track(n, net, 0.15, [(X(40), Y(yy)), (X(48), Y(yy))])
        b.tp(X(48), Y(yy), net, n)
    for dx in (-1.5, 1.5):
        for dy in (-1.5, 1.5):
            b.via(X(40 + dx), Y(15 + dy), "GND", 0.5, 0.25)
    negative(b, "RP-004", ["VD_P", "VD_N"], 1, (X(40), Y(15)), "via_transition", "four GND return vias within 2.2 mm")
    # transition without a return via, L1 -> L8
    b.via(X(55), Y(35), "NR", 0.5, 0.25)
    b.track(1, "NR", w, [(X(47), Y(35)), (X(55), Y(35))])
    b.tp(X(47), Y(35), "NR")
    b.track(n, "NR", w, [(X(55), Y(35)), (X(63), Y(35))])
    b.tp(X(63), Y(35), "NR", n)
    erc(b, "RP-004", ["NR"], 1, (X(55), Y(35)), 1.0, "via_transition",
        "L1 -> L8 transition (reference L2 -> L7) with no GND via within 20 mm")
    # blind / buried / micro vias (format coverage)
    b.via(X(30), Y(48), "BLIND1", 0.6, 0.3, span=(1, 3), kind="blind")
    b.via(X(36), Y(48), "BURIED1", 0.6, 0.3, span=(3, 6), kind="buried")
    b.via(X(42), Y(48), "UVIA1", 0.3, 0.1, span=(1, 2), kind="micro")
    b.exp["geometry"]["via_transition"] = {
        "origin_mm": [x0, y0], "stack_layers": n,
        "single_ended": {
            "net": "SE1", "via_mm": [X(15), Y(15)], "via_size": metres(0.6), "drill": metres(0.3),
            "span": ["L1", f"L{n}"], "signal_layers": ["L1", "L6"], "stub_layers": ["L7", f"L{n}"],
            "stub_length": metres(stub * 1e3),
            "stub_definition": "z_bottom(L6) - z_bottom(L8) (bottom of the barrel)",
            "return_via_mm": [X(15), Y(16.5)]},
        "single_ended_backdrilled": {
            "net": "SE2", "via_mm": [X(15), Y(35)], "span": ["L1", f"L{n}"], "signal_layers": ["L1", "L6"],
            "backdrill": {"side": "bottom", "size": metres(0.6), "kicad_layers": ["B.Cu", b.L(n - 1)],
                          "end_layer": f"L{n - 1}", "end_layer_cut": True, "cut_layers": bd_cut,
                          "kicad_token": "tertiary_drill",
                          "residual_barrel_below_last_used_layer": metres((z_bot("L6") - z_top(f"L{n - 1}")) * 1e3),
                          "residual_definition": "z_bottom(L6) - z_top(L7): barrel left between the last used layer "
                                                 "and the drill-end layer; conductor layers left in the stub: none"},
            "return_via_mm": [X(15), Y(36.5)]},
        "differential": {"p_net": "VD_P", "n_net": "VD_N", "via_mm": [[X(40), Y(14.5)], [X(40), Y(15.5)]],
                         "span": ["L1", f"L{n}"], "signal_layers": ["L1", f"L{n}"], "via_pitch": metres(1.0),
                         "return_vias_mm": [[X(38.5), Y(13.5)], [X(41.5), Y(13.5)], [X(38.5), Y(16.5)], [X(41.5), Y(16.5)]]},
        "no_return_via": {"net": "NR", "via_mm": [X(55), Y(35)], "signal_layers": ["L1", f"L{n}"],
                          "nearest_gnd_via_distance": metres(20.0), "nearest_gnd_via_distance_note": "lower bound"},
        "other_vias": {"blind": {"net": "BLIND1", "span": ["L1", "L3"]}, "buried": {"net": "BURIED1", "span": ["L3", "L6"]},
                       "micro": {"net": "UVIA1", "span": ["L1", "L2"]}}}


# ---------------------------------------------------------------- 7. power plane with loads
def power_loads(b: Board, x0: float, y0: float, plane_li: int, last_li: int, plane_rect, prefix_note: str = "") -> None:
    """VRM U5 (3 outputs) at the left of the plane, load U1 (16 power + 16 GND, 1 mm grid) at the right, 2 decaps."""
    X = lambda v: x0 + v  # noqa: E731
    Y = lambda v: y0 + v  # noqa: E731
    span = (1, last_li)

    def vp(x, y, net):
        b.via(x, y, net, 0.45, 0.2, span=span if span != (1, b.ncu) else None)

    # VRM: 3 x +1V0 on row y=24.25, 3 x GND on row y=25.75, pitch 1.5
    pads = []
    for i in range(3):
        pads.append({"n": i + 1, "type": "smd", "shape": "rect", "dx": -1.5 + 1.5 * i, "dy": -0.75, "w": 0.8, "h": 0.8, "net": "+1V0"})
    for i in range(3):
        pads.append({"n": i + 4, "type": "smd", "shape": "rect", "dx": -1.5 + 1.5 * i, "dy": 0.75, "w": 0.8, "h": 0.8, "net": "GND"})
    b.footprint("U5", X(15), Y(25), pads, libid="OA_Test:VRM_3out")
    for p in pads:
        vp(X(15) + p["dx"], Y(25) + p["dy"], p["net"])
    # load U1: 8 columns x 4 rows on a 1 mm grid, checkerboard +1V0 / GND
    pads = []
    k = 0
    pins_p, pins_g = [], []
    for c in range(8):
        for r in range(4):
            k += 1
            net = "+1V0" if (c + r) % 2 == 0 else "GND"
            dx, dy = -3.5 + c, -1.5 + r
            pads.append({"n": k, "type": "smd", "shape": "rect", "dx": dx, "dy": dy, "w": 0.6, "h": 0.6, "net": net})
            (pins_p if net == "+1V0" else pins_g).append([X(44) + dx, Y(25) + dy])
    b.footprint("U1", X(44), Y(25), pads, libid="OA_Test:Load_8x4")
    for p in pads:
        vp(X(44) + p["dx"], Y(25) + p["dy"], p["net"])
    # decaps: pad 1 (GND) / pad 2 (+1V0), 1.0 mm apart; +1V0 pad is the one nearer U1
    for ref, cx in (("C1", 39.0), ("C2", 28.5)):
        pads = [{"n": 1, "type": "smd", "shape": "rect", "dx": -0.5, "dy": 0, "w": 0.5, "h": 0.6, "net": "GND"},
                {"n": 2, "type": "smd", "shape": "rect", "dx": 0.5, "dy": 0, "w": 0.5, "h": 0.6, "net": "+1V0"}]
        b.footprint(ref, X(cx), Y(23.5), pads, libid="OA_Test:C_0402")
        vp(X(cx) - 0.5, Y(23.5), "GND")
        vp(X(cx) + 0.5, Y(23.5), "+1V0")
    px0, py0, px1, py1 = plane_rect
    nearest = lambda cx: min(math.hypot(X(cx) - px, Y(23.5) - py) for px, py in pins_p)  # noqa: E731
    d1, d2 = nearest(39.0), nearest(28.5)
    b.exp["geometry"]["power_plane"] = {
        "plane_layer": f"L{plane_li}", "plane_net": "+1V0", "plane_outline_mm": [px0, py0, px1, py1],
        "plane_size": [metres(px1 - px0), metres(py1 - py0)], "plane_area": round((px1 - px0) * (py1 - py0) * 1e-6, 12),
        "reference_layer": "L2", "reference_net": "GND",
        "vrm": {"ref": "U5", "centre_mm": [X(15), Y(25)], "output_pins": 3, "net": "+1V0", "gnd_pins": 3},
        "load": {"ref": "U1", "centre_mm": [X(44), Y(25)], "power_pins": 16, "gnd_pins": 16, "grid_pitch": metres(1.0),
                 "power_pin_mm": pins_p, "gnd_pin_mm": pins_g},
        "decaps": [{"ref": "C1", "centre_mm": [X(39.0), Y(23.5)], "distance_to_nearest_power_pin": metres(d1)},
                   {"ref": "C2", "centre_mm": [X(28.5), Y(23.5)], "distance_to_nearest_power_pin": metres(d2)}],
        "distance_definition": "decap centre to the nearest U1 +1V0 pad centre",
        "via": {"size": metres(0.45), "drill": metres(0.2), "span": ["L1", f"L{last_li}"]}}
    erc(b, "PD-001", ["+1V0"], 1, (X(28.5), Y(23.5)), 1.0, "power_loads",
        f"C2 is {d2:.1f} mm from the nearest U1 power pin (limit: C1 at {d1:.1f} mm passes)")
    negative(b, "PD-001", ["+1V0"], 1, (X(39.0), Y(23.5)), "power_loads", f"C1 is {d1:.1f} mm from the nearest U1 power pin")
