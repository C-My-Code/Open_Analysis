# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: E501
"""Impedance validation: closed form, BEM and FEM against exact, published and cross-solver values."""

from __future__ import annotations

import math
import sys
import time

from val_model import REPO, Case, case_dir, load_case

sys.path.insert(0, str(REPO / "packages" / "oa-impedance" / "tests"))
sys.path.insert(0, str(REPO / "packages" / "oa-impedance" / "tests" / "fem"))

import _xs  # test helper of oa-impedance: parametric cross-sections
from fem_crosscheck import impedance_of

from oa_impedance import closed_form as cf
from oa_impedance.bem import MeshParams
from oa_impedance.fem import FemParams, solve_fem
from oa_impedance.service import ImpedanceServiceImpl
from oa_model.xsection import CrossSection
from oa_testkit import xsections as tk

CAT_EXACT = "impedance/exact"
CAT_PUB = "impedance/published"
CAT_BEMFEM = "impedance/bem-vs-fem"


def _tag(x: float) -> str:
    return f"{x:g}".replace(".", "p")


def _bem(xs: CrossSection, mesh: MeshParams | None = None) -> tuple[str, float, float]:
    r = ImpedanceServiceImpl(mesh=mesh).solve(xs, [0.0], method="bem")
    return impedance_of(r)


def _mixed(xs: CrossSection) -> tuple[float, float]:
    r = ImpedanceServiceImpl().solve(xs, [0.0], method="bem")
    assert r.params.z_diff is not None and r.params.z_comm is not None
    return float(r.params.z_diff.array[0]), float(r.params.z_comm.array[0])


# ------------------------------------------------------------------------ exact solutions
def exact_cases() -> list[Case]:
    out: list[Case] = []
    # 1. closed form vs exact (Cohn, CPW, edge-coupled stripline) and BEM true thin strips
    d = case_dir("impedance_bem", "cohn_thin_stripline")
    g = load_case("impedance_bem", "cohn_thin_stripline")
    src = g["source"]
    tol = g["tolerance"]["rtol"]
    for er_key, er in (("z0_er1", 1.0), ("z0_er4p3", 4.3)):
        for w, exp in zip(g["inputs"]["w_over_b"], g["expected"][er_key], strict=True):
            tag = f"stripline.w{_tag(w)}.er{_tag(er)}"
            t0 = time.perf_counter()
            got = _bem(_xs.stripline(w, er, g["inputs"]["t_over_b"]))[1]
            out.append(
                Case(
                    f"bem.cohn.{tag}",
                    CAT_EXACT,
                    f"BEM thin stripline W/b={w}, er={er}",
                    src,
                    "analytic",
                    True,
                    "Z0",
                    "ohm",
                    exp,
                    got,
                    "rel",
                    tol,
                    d,
                    runtime_s=time.perf_counter() - t0,
                )
            )
            out.append(
                Case(
                    f"cf.cohn.{tag}",
                    CAT_EXACT,
                    f"Closed form Cohn stripline W/b={w}, er={er}",
                    src,
                    "analytic",
                    True,
                    "Z0",
                    "ohm",
                    exp,
                    cf.cohn_z0(w, er),
                    "rel",
                    1e-6,
                    d,
                    note="exact zero-thickness formula vs implementation",
                )
            )
    g = load_case("impedance_bem", "cohn_thin_edge_coupled")
    d, src, tol = (
        case_dir("impedance_bem", "cohn_thin_edge_coupled"),
        g["source"],
        g["tolerance"]["rtol"],
    )
    for i, s in enumerate(g["inputs"]["s_over_b"]):
        t0 = time.perf_counter()
        zd, zc = _mixed(_xs.edge_coupled_stripline(0.5, s, 1.0, g["inputs"]["t_over_b"]))
        dt = time.perf_counter() - t0
        for q, exp, got in (
            ("Zdiff", g["expected"]["zdiff"][i], zd),
            ("Zcomm", g["expected"]["zcomm"][i], zc),
        ):
            out.append(
                Case(
                    f"bem.cohn_ec.s{_tag(s)}.{q.lower()}",
                    CAT_EXACT,
                    f"BEM thin edge-coupled stripline W/b=0.5, S/b={s}",
                    src,
                    "analytic",
                    True,
                    q,
                    "ohm",
                    exp,
                    got,
                    "rel",
                    tol,
                    d,
                    runtime_s=dt,
                )
            )
        r = cf.edge_coupled_stripline(0.5e-3, s * 1e-3, 1e-3, 0.0, 1.0)
        out.append(
            Case(
                f"cf.cohn_ec.s{_tag(s)}.zdiff",
                CAT_EXACT,
                f"Closed form edge-coupled stripline S/b={s}",
                src,
                "analytic",
                True,
                "Zdiff",
                "ohm",
                g["expected"]["zdiff"][i],
                2.0 * r.z0o,
                "rel",
                1e-6,
                d,
            )
        )
    g = load_case("impedance_bem", "cpw_halfspace")
    d, src = case_dir("impedance_bem", "cpw_halfspace"), g["source"]
    for i, er in enumerate(g["inputs"]["er"]):
        xs = _xs.cpw_halfspace(er, depth=g["inputs"]["depth"])
        r = ImpedanceServiceImpl().solve(xs, [0.0], method="bem")
        assert r.params.z0 is not None
        out.append(
            Case(
                f"bem.cpw.er{_tag(er)}.z0",
                CAT_EXACT,
                f"BEM zero-thickness CPW S=1 G=0.5, er={er}",
                src,
                "analytic",
                True,
                "Z0",
                "ohm",
                g["expected"]["z0"][i],
                float(r.params.z0.array[0]),
                "rel",
                g["tolerance"]["rtol"],
                d,
            )
        )
        out.append(
            Case(
                f"bem.cpw.er{_tag(er)}.eeff",
                CAT_EXACT,
                f"BEM CPW eeff, er={er}",
                src,
                "analytic",
                True,
                "eeff",
                "",
                g["expected"]["eeff_halfspace"][i],
                float(r.params.eps_eff_modal.array[0, 0]),
                "rel",
                g["tolerance"]["per_key"]["eeff_halfspace"]["rtol"],
                d,
                note="finite substrate depth 8 mm vs half-space formula",
            )
        )
    g = load_case("impedance_closedform", "cpw_halfspace")
    d, src = case_dir("impedance_closedform", "cpw_halfspace"), g["source"]
    for i, er in enumerate(g["inputs"]["er"]):
        out.append(
            Case(
                f"cf.cpw.er{_tag(er)}.z0",
                CAT_EXACT,
                f"Closed form CPW S=1 G=0.5, er={er}",
                src,
                "analytic",
                True,
                "Z0",
                "ohm",
                g["expected"]["z0"][i],
                cf.cpw(1.0, 0.5, 0.0, er).z0,
                "abs",
                g["tolerance"]["atol"],
                d,
                note="ROADMAP: 120.57 / 51.88 ohm (Ghione-Naldi conformal mapping)",
            )
        )
    # 2. round conductors and the HJ rows from the testkit
    for c in tk.all_cases():
        if c.name.startswith(("coax", "wire_over", "two_wire")):
            q, got, _e = _bem(c.xs)
            exp = c.expected.get("z0", c.expected.get("zdiff"))
            out.append(
                Case(
                    f"bem.{c.name}",
                    CAT_EXACT,
                    f"BEM {c.name}",
                    c.source,
                    "analytic",
                    True,
                    q.upper() if q == "z0" else "Zdiff",
                    "ohm",
                    exp,
                    got,
                    "rel",
                    1e-3 if c.name.startswith("coax") else 5e-3,
                    "packages/oa-testkit (xsections.py)",
                    note="ROADMAP: BEM <= 0.1 % on coax, <= 0.5 % elsewhere",
                )
            )
    for c in tk.all_cases():
        if c.name.startswith("hj_"):
            _q, got, _e = _bem(c.xs)
            out.append(
                Case(
                    f"bem.{c.name}",
                    CAT_PUB,
                    f"BEM vs Hammerstad-Jensen formula, {c.name}",
                    "Hammerstad and Jensen, MTT-S Symp. Dig. 1980, pp. 407-409; reference values "
                    "computed by oa_impedance itself (self-computed, docs/FOLLOWUPS.md WP-000C)",
                    "self-computed",
                    False,
                    "Z0",
                    "ohm",
                    c.expected["z0"],
                    got,
                    "rel",
                    c.rtol,
                    "tests/golden/impedance_closedform/microstrip_hj_wheeler_table",
                    note="HJ is itself ~1 % accurate; this is formula-vs-field-solver agreement, "
                    "the HJ numbers are not an independent reference",
                )
            )
    # 3. implementation lock of HJ/Wheeler (self-computed)
    g = load_case("impedance_closedform", "microstrip_hj_wheeler_table")
    d, h = case_dir("impedance_closedform", "microstrip_hj_wheeler_table"), g["inputs"]["h"]
    for (u, er), key in zip(g["inputs"]["rows"], g["expected"], strict=True):
        e = g["expected"][key]
        hj, wh = cf.microstrip_hj(u * h, h, 0.0, er), cf.microstrip_wheeler(u * h, h, 0.0, er)
        for q, exp, got in (("HJ Z0", e["hj_z0"], hj.z0), ("Wheeler Z0", e["wheeler_z0"], wh.z0)):
            out.append(
                Case(
                    f"cf.lock.{key}.{q.split()[0].lower()}",
                    CAT_EXACT,
                    f"{q} lock, W/h={u}, er={er}",
                    g["source"],
                    "self-computed",
                    False,
                    q,
                    "ohm",
                    exp,
                    got,
                    "rel",
                    1e-6,
                    d,
                    note="implementation lock, regression only",
                )
            )
    return out


# ------------------------------------------------------------------------ published
def published_cases() -> list[Case]:
    out: list[Case] = []
    g = load_case("published", "ipc2141_microstrip_legacy")
    i = g["inputs"]
    h, t, er, w = i["h"], i["t"], i["er"], i["w"]
    independent_value = 87.0 / math.sqrt(er + 1.41) * math.log(5.98 * h / (0.8 * w + t))
    out.append(
        Case(
            "pub.ipc2141.microstrip",
            CAT_PUB,
            "IPC-2141 legacy microstrip formula",
            g["source"],
            "published",
            True,
            "Z0",
            "ohm",
            g["expected"]["z0"],
            cf.ipc2141_microstrip(w, h, t, er).z0,
            "abs",
            g["tolerance"]["atol"],
            case_dir("published", "ipc2141_microstrip_legacy"),
            note=f"hand-evaluated formula value {independent_value:.4f} ohm; ROADMAP quotes 36.8",
        )
    )
    g = load_case("published", "wheeler_1977_microstrip_report")
    i = g["inputs"]
    out.append(
        Case(
            "pub.wheeler1977.report",
            CAT_PUB,
            "Wheeler 1977 microstrip, report value",
            g["source"],
            "self-computed",
            False,
            "Z0",
            "ohm",
            g["expected"]["z0"],
            cf.microstrip_wheeler(i["w"], i["h"], i["t"], i["er"]).z0,
            "abs",
            g["tolerance"]["atol"],
            case_dir("published", "wheeler_1977_microstrip_report"),
            note="value from docs/research/02, not recomputed independently",
        )
    )
    g = load_case("published", "pozar_ex3_6_microstrip_50ohm")
    i = g["inputs"]
    d_ = case_dir("published", "pozar_ex3_6_microstrip_50ohm")
    r = cf.microstrip_hj(i["w_over_d"] * i["d"], i["d"], 0.0, i["er"])
    for q, exp, got, unit in (
        ("Z0", g["expected"]["z0"], r.z0, "ohm"),
        ("eeff", g["expected"]["eeff"], r.eeff, ""),
    ):
        out.append(
            Case(
                f"pub.pozar_ex3_6.{q.lower()}",
                CAT_PUB,
                f"Pozar Example 3.6 [verify], {q}",
                g["source"],
                "published",
                True,
                q,
                unit,
                exp,
                got,
                "rel",
                g["tolerance"]["rtol"],
                d_,
                note="[verify] citation unchecked, excluded from pass counts",
                forced_status="verify",
            )
        )
    for name in (
        "wadell_worked_examples",
        "polar_si9000_app_notes",
        "bryant_weiss_microstrip_tables",
        "tnt_mmtl_atlc_offline",
    ):
        g = load_case("published", f"pending_{name}")
        out.append(
            Case(
                f"pub.pending.{name}",
                CAT_PUB,
                f"[verify] {name.replace('_', ' ')} (need {g['inputs']['required_cases']} cases)",
                g["source"],
                "published",
                True,
                "slot",
                "",
                None,
                None,
                "exact",
                0.0,
                case_dir("published", f"pending_{name}"),
                note=g["meta"]["note"],
                forced_status="pending",
            )
        )
    return out


# ------------------------------------------------------------------------ BEM vs FEM
def _generated_structures() -> list[tuple[str, CrossSection]]:
    """The 50 deterministic structures of oa-impedance's test_crosscheck (kept identical)."""
    out: list[tuple[str, CrossSection]] = []
    for er in (2.2, 4.3, 9.8):
        for w in (0.4, 0.8, 1.5, 3.0):
            out.append((f"ms_w{w}_er{er}", _xs.microstrip(w, er, t_over_h=0.01)))
    for er in (1.0, 3.5, 4.3):
        for w in (0.2, 0.5, 1.0, 2.0):
            out.append((f"sl_w{w}_er{er}", _xs.stripline(w, er, t_over_b=0.02)))
    for er in (3.5, 4.3, 9.8):
        for u, g in ((0.5, 0.3), (1.0, 0.5), (1.5, 1.0), (2.0, 0.25)):
            out.append((f"cms_u{u}_g{g}_er{er}", _xs.edge_coupled_microstrip(u, g, er, 0.01)))
    for s in (0.15, 0.3, 0.6, 1.0):
        for er in (3.5, 4.3, 9.8):
            out.append((f"csl_s{s}_er{er}", _xs.edge_coupled_stripline(0.4, s, er, 0.02)))
    out.append(("pcb_single", _xs.pcb_microstrip(df=0.0)))
    out.append(("pcb_pair", _xs.pcb_microstrip(pair=True, df=0.0)))
    return out


def _pair_cases(
    cid: str,
    title: str,
    xs: CrossSection,
    bem_mesh: MeshParams | None,
    fem: FemParams,
    src: str,
    cdir: str,
    with_eeff: bool,
    rtol_z: float,
    rtol_zd: float,
) -> list[Case]:
    t0 = time.perf_counter()
    q, zf, ef = impedance_of(solve_fem(xs, [0.0], fem))
    dt = time.perf_counter() - t0
    _, zb, eb = _bem(xs, bem_mesh)
    diff = q == "zdiff"
    out = [
        Case(
            f"{cid}.{q}",
            CAT_BEMFEM,
            f"{title}: {'Zdiff' if diff else 'Z0'} BEM vs FEM",
            src,
            "cross-solver",
            False,
            "Zdiff" if diff else "Z0",
            "ohm",
            zf,
            zb,
            "rel",
            rtol_zd if diff else rtol_z,
            cdir,
            runtime_s=dt,
        )
    ]
    if with_eeff:
        gap = 0.005 < abs(eb / ef - 1) <= 0.007
        out.append(
            Case(
                f"{cid}.eeff",
                CAT_BEMFEM,
                f"{title}: eeff BEM vs FEM",
                src,
                "cross-solver",
                False,
                "eeff",
                "",
                ef,
                eb,
                "rel",
                0.005,
                cdir,
                note="spec 0.5 %; WP-202 accepted 0.7 % (BEM is the low one, docs/FOLLOWUPS.md)"
                if gap
                else "",
                forced_status="known_gap" if gap else None,
            )
        )
    return out


def bem_vs_fem_testkit() -> list[Case]:
    g = load_case("crosssolver", "bem_vs_fem_testkit")
    cdir = case_dir("crosssolver", "bem_vs_fem_testkit")
    rz, rzd = g["tolerance"]["rtol"], g["tolerance"]["per_key"]["zdiff"]["rtol"]
    out: list[Case] = []
    for c in tk.all_cases():
        out += _pair_cases(
            f"x.testkit.{c.name}",
            c.name,
            c.xs,
            None,
            FemParams(loss="none"),
            g["source"],
            cdir,
            False,
            rz,
            rzd,
        )
    # FEM against the exact solutions that have no thin-strip approximation
    for c in tk.all_cases():
        if c.name.startswith(("coax", "wire_over", "two_wire")):
            q, zf, _e = impedance_of(solve_fem(c.xs, [0.0], FemParams(loss="none")))
            exp = c.expected.get("z0", c.expected.get("zdiff"))
            out.append(
                Case(
                    f"fem.{c.name}",
                    CAT_EXACT,
                    f"FEM {c.name}",
                    c.source,
                    "analytic",
                    True,
                    "Z0" if q == "z0" else "Zdiff",
                    "ohm",
                    exp,
                    zf,
                    "rel",
                    1e-3 if c.name.startswith("coax") else 3e-3,
                    "packages/oa-testkit (xsections.py)",
                    note="WP-202 gate: FEM <= 0.1 % on coax, <= 0.3 % elsewhere",
                )
            )
    return out


def bem_vs_fem_50() -> list[Case]:
    g = load_case("crosssolver", "bem_vs_fem_50_structures")
    cdir = case_dir("crosssolver", "bem_vs_fem_50_structures")
    rz, rzd = g["tolerance"]["rtol"], g["tolerance"]["per_key"]["zdiff"]["rtol"]
    structures = _generated_structures()
    assert len(structures) == 50
    fem = FemParams(loss="none", rtol=5e-4)
    mesh = MeshParams().scaled(4.0)
    out: list[Case] = []
    for name, xs in structures:
        out += _pair_cases(f"x.s50.{name}", name, xs, mesh, fem, g["source"], cdir, True, rz, rzd)
    return out


def run(include_sweep: bool = True) -> list[Case]:
    cases = exact_cases() + published_cases() + bem_vs_fem_testkit()
    if include_sweep:
        cases += bem_vs_fem_50()
    return cases
