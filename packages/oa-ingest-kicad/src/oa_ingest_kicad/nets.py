# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Net kinds, net classes from ``.kicad_pro`` and the differential-pair naming heuristic."""

from __future__ import annotations

import fnmatch
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from oa_model import Diagnostic, DiffPair, Net, NetClass, SourceRef

MM = 1e-3

_GROUND = re.compile(
    r"^(?:[ADPEFCS]?GND[A-Z0-9_.\-+]*|[ADP]?VSS[A-Z0-9_.\-+]*|GROUND|0V|COM|SHIELD)$",
    re.IGNORECASE,
)
_POWER = re.compile(
    r"^(?:[+\-]?\d+V\d*[A-Z0-9_.]*|[+\-]?\d+(?:\.\d+)?V|VCC[A-Z0-9_.\-+]*|VDD[A-Z0-9_.\-+]*|"
    r"VBUS[A-Z0-9_.\-+]*|VIN[A-Z0-9_.\-+]*|VBAT[A-Z0-9_.\-+]*|VEE[A-Z0-9_.\-+]*|"
    r"VREF[A-Z0-9_.\-+]*|VOUT[A-Z0-9_.\-+]*|VDDIO[A-Z0-9_.\-+]*|AVDD[A-Z0-9_.\-+]*|"
    r"DVDD[A-Z0-9_.\-+]*|[+\-]V[A-Z0-9_.]+|V[+\-]|VPP[A-Z0-9_.\-+]*)$",
    re.IGNORECASE,
)


def net_kind(name: str) -> str:
    """Heuristic net kind from the last component of the hierarchical name."""
    base = name.rsplit("/", 1)[-1].strip()
    if not base:
        return "unknown"
    if _GROUND.match(base):
        return "ground"
    if _POWER.match(base):
        return "power"
    return "signal"


# ----------------------------------------------------------------------------------------
# diff pairs
# ----------------------------------------------------------------------------------------
# (positive suffix, negative suffix), most specific first. Matching is case-insensitive.
_PAIR_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("_DP", "_DN"),
    ("_P", "_N"),
    ("_T", "_C"),
    ("+", "-"),
    ("P", "N"),
)


def find_diff_pairs(names: Iterable[str]) -> list[tuple[str, str, str]]:
    """Deterministic ``(base, p_net, n_net)`` triples from net names."""
    by_upper = {n.upper(): n for n in names}
    used: set[str] = set()
    found: list[tuple[str, str, str]] = []
    for name in sorted(by_upper.values()):
        up = name.upper()
        if name in used:
            continue
        for ps, ns in _PAIR_SUFFIXES:
            if up.endswith(ps) and len(up) > len(ps):
                base = up[: -len(ps)]
                other = by_upper.get(base + ns)
                if other is None or other == name or other in used:
                    continue
                # bare "P"/"N" suffixes need a separator or a digit-less alnum base to avoid
                # pairing unrelated nets such as VCCP / VCCN; require base to end in a digit,
                # a separator or '_'
                if ps in ("P", "N") and not re.search(r"[\d_\-./]$", base):
                    continue
                used.add(name)
                used.add(other)
                found.append((name[: len(name) - len(ps)], name, other))
                break
    return found


def make_diff_pairs(
    names: Iterable[str], classes: dict[str, NetClass], net_class_of: dict[str, str | None]
) -> tuple[DiffPair, ...]:
    pairs: list[DiffPair] = []
    ids: set[str] = set()
    for base, p, n in find_diff_pairs(names):
        pid = f"dp:{base}"
        k = 2
        while pid in ids:
            pid = f"dp:{base}#{k}"
            k += 1
        ids.add(pid)
        z = None
        for net in (p, n):
            cname = net_class_of.get(net)
            if cname and cname in classes and classes[cname].target_z_diff:
                z = classes[cname].target_z_diff
                break
        pairs.append(DiffPair(id=pid, p_net=p, n_net=n, origin="naming", target_z_diff=z))
    return tuple(pairs)


# ----------------------------------------------------------------------------------------
# .kicad_pro
# ----------------------------------------------------------------------------------------
_OHM = re.compile(r"(\d+(?:\.\d+)?)\s*(?:ohm|ohms|Ω|r)(?![a-z])", re.IGNORECASE)
_DIFF_HINT = re.compile(r"diff|(?:^|[_\-\s])dp(?:$|[_\-\s])|pair|lvds|usb|pcie|hdmi", re.IGNORECASE)
_DIFF_TYPICAL = (80.0, 85.0, 90.0, 100.0, 120.0)


def parse_impedance_from_name(name: str) -> tuple[float | None, float | None]:
    """``(z_se, z_diff)`` from a class name such as ``50ohm``, ``90Ohm-diff_CSI`` or ``100R``."""
    m = _OHM.search(name)
    if not m:
        return None, None
    z = float(m.group(1))
    if z <= 0 or z > 1000:
        return None, None
    if _DIFF_HINT.search(name) or z in _DIFF_TYPICAL:
        return None, z
    return z, None


def _mm(v: object) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
        return float(v) * MM
    return None


@dataclass
class ProjectNets:
    classes: tuple[NetClass, ...] = ()
    patterns: tuple[tuple[str, str], ...] = ()  # (glob, class)
    assignments: dict[str, tuple[str, ...]] = None  # type: ignore[assignment]
    priorities: dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.assignments is None:
            self.assignments = {}
        if self.priorities is None:
            self.priorities = {}

    def class_of(self, net: str) -> str | None:
        """Explicit assignment, else the first matching pattern, else ``Default``."""
        if not self.classes:
            return None
        cand = self.assignments.get(net)
        if cand:
            return min(cand, key=lambda c: self.priorities.get(c, 1 << 30))
        for glob, cname in self.patterns:
            if fnmatch.fnmatchcase(net, glob.replace("[", "[[]")):
                return cname
        return "Default" if any(c.name == "Default" for c in self.classes) else None


def read_project(path: Path, diags: list[Diagnostic]) -> ProjectNets:
    """Parse ``net_settings`` of a ``.kicad_pro``. Problems become diagnostics, never raise."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        diags.append(
            Diagnostic(
                severity="warning",
                code="INGEST.PROJECT_UNREADABLE",
                message=f"cannot read project file {path.name}: {exc}",
                hint="check that the .kicad_pro is valid JSON; net classes are skipped",
            )
        )
        return ProjectNets()
    ns = data.get("net_settings") if isinstance(data, dict) else None
    if not isinstance(ns, dict):
        return ProjectNets()
    raw_classes = ns.get("classes") or []
    patterns_by_class: dict[str, list[str]] = {}
    patterns: list[tuple[str, str]] = []
    for p in ns.get("netclass_patterns") or []:
        if isinstance(p, dict) and isinstance(p.get("pattern"), str) and p.get("netclass"):
            patterns.append((p["pattern"], p["netclass"]))
            patterns_by_class.setdefault(p["netclass"], []).append(p["pattern"])
    classes: list[NetClass] = []
    prio: dict[str, int] = {}
    for c in raw_classes:
        if not isinstance(c, dict) or not isinstance(c.get("name"), str):
            continue
        name = c["name"]
        z_se, z_diff = parse_impedance_from_name(name)
        tp = c.get("tuning_profile")
        classes.append(
            NetClass(
                name=name,
                track_width=_mm(c.get("track_width")),
                clearance=_mm(c.get("clearance")),
                via_diameter=_mm(c.get("via_diameter")),
                via_drill=_mm(c.get("via_drill")),
                dp_width=_mm(c.get("diff_pair_width")),
                dp_gap=_mm(c.get("diff_pair_gap")),
                target_z_se=z_se,
                target_z_diff=z_diff,
                tuning_profile=tp if isinstance(tp, str) and tp else None,
                patterns=tuple(patterns_by_class.get(name, ())),
                source=SourceRef(tool="kicad", token="netclass", name=name),
            )
        )
        if isinstance(c.get("priority"), int):
            prio[name] = c["priority"]
    assign: dict[str, tuple[str, ...]] = {}
    raw_assign = ns.get("netclass_assignments")
    if isinstance(raw_assign, dict):
        for net, v in raw_assign.items():
            if isinstance(v, str):
                assign[net] = (v,)
            elif isinstance(v, list):
                assign[net] = tuple(x for x in v if isinstance(x, str))
    return ProjectNets(tuple(classes), tuple(patterns), assign, prio)


def find_project(pcb: Path) -> tuple[Path | None, bool]:
    """``(.kicad_pro, exact)``. Same stem first, else the only ``.kicad_pro`` in the directory."""
    same = pcb.with_suffix(".kicad_pro")
    if same.is_file():
        return same, True
    others = sorted(pcb.parent.glob("*.kicad_pro"))
    if len(others) == 1:
        return others[0], False
    return None, True


def build_nets(
    names: Iterable[str], proj: ProjectNets
) -> tuple[tuple[Net, ...], dict[str, str | None]]:
    cls_of: dict[str, str | None] = {}
    nets: list[Net] = []
    for name in sorted(set(names)):
        c = proj.class_of(name)
        cls_of[name] = c
        nets.append(Net(id=name, kind=net_kind(name), net_class=c))  # type: ignore[arg-type]
    return tuple(nets), cls_of
