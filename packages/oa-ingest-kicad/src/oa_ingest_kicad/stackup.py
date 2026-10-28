# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``(setup (stackup ...))`` and the ``(layers ...)`` table to :class:`oa_model.Stackup`."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from oa_model import (
    ConductorLayer,
    ConductorMaterial,
    ConstantDkDf,
    DielectricLayer,
    DielectricMaterial,
    Diagnostic,
    MaskLayer,
    Stackup,
)

from . import sexpr

DEFAULT_COPPER_T = 35e-6  # metres
DEFAULT_MASK_T = 10e-6
DEFAULT_DK = 4.5
DEFAULT_DF = 0.02
MASK_DK = 3.3
MASK_DF = 0.0
F_REF = 1e9

_CU_NAME = re.compile(r"^(F|B|In(\d+))\.Cu$")


def copper_sort_key(name: str) -> tuple[int, int]:
    """Canonical top-to-bottom order of KiCad copper layer names: F.Cu, In1..InN, B.Cu."""
    if name == "F.Cu":
        return (0, 0)
    if name == "B.Cu":
        return (2, 0)
    m = _CU_NAME.match(name)
    return (1, int(m.group(2))) if m and m.group(2) else (1, 10_000)


@dataclass(frozen=True)
class CopperInfo:
    name: str  # "In1.Cu"
    ltype: str  # signal | power | mixed | jumper
    user_name: str | None


@dataclass
class StackupResult:
    stackup: Stackup
    layer_ids: dict[str, str]  # copper name -> "L<n>"
    copper_order: list[str]  # top to bottom
    diagnostics: list[Diagnostic] = field(default_factory=list)


@dataclass
class _Ply:
    thickness: float | None = None
    material: str | None = None
    eps: float | None = None
    tan: float | None = None


def read_layer_table(layers_node: sexpr.Node | None) -> dict[str, CopperInfo]:
    """Copper layers from ``(layers (ord "name" type ["alias"]) ...)``, keyed by name."""
    out: dict[str, CopperInfo] = {}
    if layers_node is None:
        return out
    for e in layers_node[1:]:
        if not isinstance(e, list) or len(e) < 3:
            continue
        # e == [ordinal, "name", type, ("alias")]
        name = e[1] if isinstance(e[1], str) else ""
        if not name.endswith(".Cu"):
            continue
        ltype = e[2] if isinstance(e[2], str) else "signal"
        alias = e[3] if len(e) > 3 and isinstance(e[3], str) else None
        out[name] = CopperInfo(name, ltype, alias)
    return out


def _ply_list(layer: sexpr.Node) -> list[_Ply]:
    """Split a dielectric ``(layer ...)`` node into plies at every ``addsublayer`` marker."""
    plies = [_Ply()]
    for c in layer[2:]:
        if isinstance(c, str):
            if c == "addsublayer":
                plies.append(_Ply())
            continue
        if not isinstance(c, list) or not c:
            continue
        h = c[0]
        if h == "addsublayer":  # tolerate a nested form: (addsublayer (thickness ..) ...)
            plies.append(_Ply())
            for cc in c[1:]:
                if isinstance(cc, list) and cc:
                    _set_ply(plies[-1], cc)
            continue
        _set_ply(plies[-1], c)
    return plies


def _set_ply(ply: _Ply, node: sexpr.Node) -> None:
    h = node[0]
    v = node[1] if len(node) > 1 and isinstance(node[1], str) else None
    if v is None:
        return
    try:
        if h == "thickness":
            ply.thickness = float(v) * 1e-3
        elif h == "material":
            ply.material = v
        elif h == "epsilon_r":
            ply.eps = float(v)
        elif h == "loss_tangent":
            ply.tan = float(v)
    except ValueError:
        pass


class _Materials:
    """Interns dielectric materials; same name with different Dk/Df gets a suffixed name."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, float, float, str], str] = {}
        self._used: set[str] = set()
        self.items: list[DielectricMaterial] = []

    def get(self, base: str, eps: float, tan: float, source: str) -> str:
        key = (base, eps, tan, source)
        if key in self._by_key:
            return self._by_key[key]
        name = base
        if name in self._used:
            name = f"{base}_er{eps:g}_df{tan:g}"
            n = 2
            while name in self._used:
                name = f"{base}_er{eps:g}_df{tan:g}_{n}"
                n += 1
        self._used.add(name)
        self._by_key[key] = name
        self.items.append(
            DielectricMaterial(
                name=name,
                model=ConstantDkDf(dk=max(eps, 1.0), df=min(max(tan, 0.0), 0.999), f_ref=F_REF),
                source="ingest" if source == "ingest" else "default",
            )
        )
        return name


def build_stackup(
    setup: sexpr.Node | None,
    copper: dict[str, CopperInfo],
    board_thickness: float,
) -> StackupResult:
    """Build the model stackup from the file, or from KiCad defaults when it is missing/unusable."""
    diags: list[Diagnostic] = []
    node = sexpr.child(setup, "stackup") if setup is not None else None
    parsed = _parse_stackup(node, copper, diags) if node is not None else None
    known = parsed is not None
    if parsed is None:
        if node is None:
            msg = "board has no stackup section; KiCad default stackup assumed"
        else:
            msg = "stackup section does not match the copper layer table; KiCad default assumed"
        diags.append(
            Diagnostic(
                severity="warning",
                code="INGEST.STACKUP_DEFAULTED",
                message=msg,
                path="/stackup",
                hint="define the board stackup in KiCad (Board Setup > Physical Stackup) "
                "or supply a physics sidecar",
            )
        )
        parsed = _default_entries(sorted(copper, key=copper_sort_key), board_thickness)
    entries, finish = parsed

    mats = _Materials()
    cu_mat = ConductorMaterial(name="copper", source="default")
    # entries are top to bottom; z grows upward from 0 at the bottom
    z = 0.0
    tmp: list[tuple[str, dict[str, object]]] = []
    for kind, data in reversed(entries):
        t = float(data["thickness"])  # type: ignore[arg-type]
        data = dict(data)
        data["z_bottom"] = z
        z += t
        data["z_top"] = z
        tmp.append((kind, data))
    tmp.reverse()
    total = z

    layers = []
    ids: dict[str, str] = {}
    order: list[str] = []
    n_cu = n_d = 0
    for kind, d in tmp:
        if kind == "copper":
            n_cu += 1
            info = copper[str(d["name"])]
            lid = f"L{n_cu}"
            ids[info.name] = lid
            order.append(info.name)
            role = {"signal": "signal", "power": "plane", "mixed": "mixed", "jumper": "jumper"}.get(
                info.ltype, "unknown"
            )
            layers.append(
                ConductorLayer(
                    id=lid,
                    index=n_cu,
                    source_name=info.name,
                    user_name=info.user_name,
                    role=role,  # type: ignore[arg-type]
                    thickness=float(d["thickness"]),  # type: ignore[arg-type]
                    z_bottom=float(d["z_bottom"]),  # type: ignore[arg-type]
                    z_top=float(d["z_top"]),  # type: ignore[arg-type]
                    material="copper",
                )
            )
        elif kind == "dielectric":
            n_d += 1
            eps = d["eps"]
            tan = d["tan"]
            src = "ingest" if (d["eps"] is not None) else "default"
            mname = mats.get(
                str(d["material"] or "FR4"),
                float(eps) if eps is not None else DEFAULT_DK,  # type: ignore[arg-type]
                float(tan) if tan is not None else DEFAULT_DF,  # type: ignore[arg-type]
                src,
            )
            layers.append(
                DielectricLayer(
                    id=f"D{n_d}",
                    index=n_d,
                    source_name=str(d["name"]),
                    construction=d["construction"],  # type: ignore[arg-type]
                    thickness=float(d["thickness"]),  # type: ignore[arg-type]
                    z_bottom=float(d["z_bottom"]),  # type: ignore[arg-type]
                    z_top=float(d["z_top"]),  # type: ignore[arg-type]
                    material=mname,
                )
            )
        else:  # mask
            eps = d["eps"]
            tan = d["tan"]
            mname = mats.get(
                "SolderMask",
                float(eps) if eps is not None else MASK_DK,  # type: ignore[arg-type]
                float(tan) if tan is not None else MASK_DF,  # type: ignore[arg-type]
                "ingest" if eps is not None else "default",
            )
            layers.append(
                MaskLayer(
                    id="MT" if d["top"] else "MB",
                    source_name=str(d["name"]),
                    thickness=float(d["thickness"]),  # type: ignore[arg-type]
                    z_bottom=float(d["z_bottom"]),  # type: ignore[arg-type]
                    z_top=float(d["z_top"]),  # type: ignore[arg-type]
                    material=mname,
                )
            )
    stackup = Stackup(
        layers=tuple(layers),
        materials=(cu_mat, *mats.items),
        finish=finish,
        total_thickness=total,
        known=known,
    )
    return StackupResult(stackup, ids, order, diags)


Entry = tuple[str, dict[str, object]]


def _parse_stackup(
    node: sexpr.Node, copper: dict[str, CopperInfo], diags: list[Diagnostic]
) -> tuple[list[Entry], str | None] | None:
    entries: list[Entry] = []
    finish = sexpr.get_str(node, "copper_finish")
    if finish is not None and finish.lower() in ("none", ""):
        finish = None
    seen_cu: list[str] = []
    n_dielectric = 0
    for lay in sexpr.children(node, "layer"):
        name = lay[1] if len(lay) > 1 and isinstance(lay[1], str) else ""
        typ = (sexpr.get_str(lay, "type") or "").lower()
        thick = sexpr.get_float(lay, "thickness")
        if typ == "copper" or name.endswith(".Cu"):
            if name not in copper:
                return None
            seen_cu.append(name)
            t = thick * 1e-3 if thick else DEFAULT_COPPER_T
            entries.append(("copper", {"name": name, "thickness": t}))
        elif "solder mask" in typ or name.endswith(".Mask"):
            if not thick:
                continue
            eps = sexpr.get_float(lay, "epsilon_r")
            entries.append(
                (
                    "mask",
                    {
                        "name": name,
                        "thickness": thick * 1e-3,
                        "top": name.startswith("F.") or "top" in typ,
                        "eps": eps,
                        "tan": sexpr.get_float(lay, "loss_tangent"),
                    },
                )
            )
        elif typ in ("core", "prepreg", "dielectric") or name.startswith("dielectric"):
            n_dielectric += 1
            plies = _ply_list(lay)
            construction = typ if typ in ("core", "prepreg") else "unknown"
            for i, p in enumerate(plies):
                if not p.thickness or p.thickness <= 0:
                    diags.append(
                        Diagnostic(
                            severity="info",
                            code="INGEST.STACKUP_PLY_SKIPPED",
                            message=f"dielectric {name!r} ply {i + 1} has no thickness; skipped",
                            path="/stackup/layers",
                        )
                    )
                    continue
                entries.append(
                    (
                        "dielectric",
                        {
                            "name": name if i == 0 else f"{name}/{i + 1}",
                            "thickness": p.thickness,
                            "construction": construction,
                            "material": p.material,
                            "eps": p.eps,
                            "tan": p.tan,
                        },
                    )
                )
        # silkscreen / paste: not part of the model
    if sorted(seen_cu, key=copper_sort_key) != sorted(copper, key=copper_sort_key) or len(
        set(seen_cu)
    ) != len(seen_cu):
        return None
    if not any(k == "copper" for k, _ in entries):
        return None
    return entries, finish


def _default_entries(cu_names: list[str], board_thickness: float) -> tuple[list[Entry], None]:
    """KiCad's default stackup: 35 um copper, 10 um mask, equal dielectrics filling the board."""
    n = len(cu_names)
    bt = board_thickness if board_thickness > 0 else 1.6e-3
    n_d = max(n - 1, 1)
    d_t = max((bt - n * DEFAULT_COPPER_T) / n_d, 1e-6)
    entries: list[Entry] = [
        (
            "mask",
            {"name": "F.Mask", "thickness": DEFAULT_MASK_T, "top": True, "eps": None, "tan": None},
        )
    ]
    for i, name in enumerate(cu_names):
        entries.append(("copper", {"name": name, "thickness": DEFAULT_COPPER_T}))
        if i < n - 1 or n == 1:
            entries.append(
                (
                    "dielectric",
                    {
                        "name": f"dielectric {i + 1}",
                        "thickness": d_t,
                        "construction": "core" if n == 2 else "unknown",
                        "material": "FR4",
                        "eps": None,
                        "tan": None,
                    },
                )
            )
    entries.append(
        (
            "mask",
            {"name": "B.Mask", "thickness": DEFAULT_MASK_T, "top": False, "eps": None, "tan": None},
        )
    )
    return entries, None
