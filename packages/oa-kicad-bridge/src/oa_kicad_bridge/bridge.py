# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""kicad-cli wrappers and the pcbnew zone-refill subprocess (ADR-0006).

pcbnew is only ever imported inside a subprocess started with KiCad's own Python; this module and
its callers never import it.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from oa_kicad_bridge.tools import KiCadTools
from oa_model._base import OAModel
from oa_model.board import Board
from oa_model.common import Diagnostic
from oa_model.errors import ExternalToolFailed, IngestError
from oa_model.setup import ReadOptions

HELPER = Path(__file__).with_name("pcbnew_helper.py")
_FORMATS = ("auto", "pads", "altium", "eagle", "cadstar", "fabmaster", "pcad", "solidworks")


class DrcItem(OAModel):
    description: str = Field(description="Item description text from KiCad, e.g. 'Track on F.Cu'.")
    x_mm: float | None = Field(
        default=None, description="Item X position in the KiCad file frame [mm]."
    )
    y_mm: float | None = Field(
        default=None, description="Item Y position in the KiCad file frame [mm], Y down."
    )
    uuid: str | None = Field(default=None, description="KiCad item uuid.")


class DrcViolation(OAModel):
    type: str = Field(description="KiCad violation key, e.g. 'clearance', 'via_dangling'.")
    severity: Literal["error", "warning", "exclusion", "action", "debug", "info"] = Field(
        description="KiCad severity."
    )
    description: str = Field(description="Human-readable message.")
    items: tuple[DrcItem, ...] = Field(default=(), description="Items involved.")


class DrcReport(OAModel):
    kicad_version: str | None = Field(
        default=None, description="KiCad version string from the report."
    )
    coordinate_units: str = Field(default="mm", description="Unit of item positions.")
    violations: tuple[DrcViolation, ...] = Field(default=(), description="Design-rule violations.")
    unconnected_items: tuple[DrcViolation, ...] = Field(
        default=(), description="Unrouted connections."
    )
    schematic_parity: tuple[DrcViolation, ...] = Field(
        default=(), description="Schematic parity issues."
    )

    def count(self, severity: str) -> int:
        return sum(1 for v in self.violations if v.severity == severity)


class ImportResult(OAModel):
    path: Path = Field(description="Converted .kicad_pcb.")
    source_format: str | None = Field(default=None, description="Format detected by KiCad.")
    diagnostics: tuple[Diagnostic, ...] = Field(
        default=(), description="Import report as diagnostics."
    )
    statistics: dict[str, int] = Field(
        default_factory=dict, description="Item counts reported by KiCad."
    )


def _tools(tools: KiCadTools | None) -> KiCadTools:
    return tools if tools is not None else KiCadTools.discover()


# ---------------------------------------------------------------------- kicad-cli
def upgrade(path: Path, *, tools: KiCadTools | None = None, force: bool = False) -> Path:
    """Upgrade a board file to the current format IN PLACE (this is what kicad-cli does)."""
    t = _tools(tools)
    t.run_cli(["pcb", "upgrade", *(["--force"] if force else []), path])
    return Path(path)


def _violation(raw: dict[str, Any]) -> DrcViolation:
    items = tuple(
        DrcItem(
            description=str(i.get("description", "")),
            x_mm=(i.get("pos") or {}).get("x"),
            y_mm=(i.get("pos") or {}).get("y"),
            uuid=i.get("uuid"),
        )
        for i in raw.get("items", ())
    )
    return DrcViolation(
        type=str(raw.get("type", "")),
        severity=raw.get("severity", "error"),
        description=str(raw.get("description", "")),
        items=items,
    )


def drc(path: Path, *, tools: KiCadTools | None = None, refill: bool = False) -> DrcReport:
    """Run ``kicad-cli pcb drc`` (all severities) and parse the JSON report."""
    t = _tools(tools)
    with tempfile.TemporaryDirectory(prefix="oa_drc_") as d:
        out = Path(d) / "drc.json"
        args: list[str | Path] = ["pcb", "drc", "--format", "json", "--severity-all", "-o", out]
        if refill:
            args.append("--refill-zones")
        t.run_cli([*args, path])
        try:
            data = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise IngestError("kicad-cli produced no DRC report", code="INGEST.DRC_REPORT") from exc
    return DrcReport(
        kicad_version=data.get("kicad_version"),
        coordinate_units=data.get("coordinate_units", "mm"),
        violations=tuple(_violation(v) for v in data.get("violations", ())),
        unconnected_items=tuple(_violation(v) for v in data.get("unconnected_items", ())),
        schematic_parity=tuple(_violation(v) for v in data.get("schematic_parity", ())),
    )


def export_ipc2581(
    path: Path,
    out: Path,
    *,
    tools: KiCadTools | None = None,
    version: str = "C",
    precision: int = 6,
) -> Path:
    t = _tools(tools)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    t.run_cli(
        [
            "pcb",
            "export",
            "ipc2581",
            "--version",
            version,
            "--precision",
            str(precision),
            "-o",
            out,
            path,
        ]
    )
    if not Path(out).is_file():
        raise ExternalToolFailed("kicad-cli did not write the IPC-2581 file", returncode=0)
    return Path(out)


def export_odb(
    path: Path, out: Path, *, tools: KiCadTools | None = None, check_zones: bool = True
) -> Path:
    t = _tools(tools)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    args: list[str | Path] = ["pcb", "export", "odb", "--compression", "zip", "-o", out]
    if check_zones:
        args.append("--check-zones")
    t.run_cli([*args, path])
    if not Path(out).is_file():
        raise ExternalToolFailed("kicad-cli did not write the ODB++ file", returncode=0)
    return Path(out)


def _report_to_diagnostics(rep: dict[str, Any]) -> tuple[Diagnostic, ...]:
    out: list[Diagnostic] = []

    def text(x: Any) -> str:
        if isinstance(x, dict):
            return str(x.get("message") or x.get("text") or json.dumps(x, sort_keys=True))
        return str(x)

    for e in rep.get("errors", ()):
        out.append(Diagnostic(severity="error", code="INGEST.IMPORT_ERROR", message=text(e)))
    for w in rep.get("warnings", ()):
        out.append(Diagnostic(severity="warning", code="INGEST.IMPORT_WARNING", message=text(w)))
    for name, m in sorted((rep.get("layer_mapping") or {}).items()):
        if isinstance(m, dict) and m.get("method") not in (None, "auto"):
            out.append(
                Diagnostic(
                    severity="info",
                    code="INGEST.IMPORT_LAYER_MAPPING",
                    message=f"layer '{name}' mapped to {m.get('kicad_layer')} ({m.get('method')})",
                )
            )
    return tuple(out)


def import_foreign_report(
    path: Path,
    fmt: str = "auto",
    *,
    out_dir: Path | None = None,
    tools: KiCadTools | None = None,
) -> ImportResult:
    """``kicad-cli pcb import``: convert a foreign board to ``.kicad_pcb`` (report parsed)."""
    if fmt not in _FORMATS:
        raise IngestError(f"unknown import format '{fmt}'", code="INGEST.IMPORT_FORMAT")
    t = _tools(tools)
    src = Path(path)
    work = Path(out_dir) if out_dir is not None else Path(tempfile.mkdtemp(prefix="oa_import_"))
    work.mkdir(parents=True, exist_ok=True)
    out = work / (src.stem + ".kicad_pcb")
    rep_file = work / "import_report.json"
    t.run_cli(
        [
            "pcb",
            "import",
            "--format",
            fmt,
            "--report-format",
            "json",
            "--report-file",
            rep_file,
            "-o",
            out,
            src,
        ]
    )
    rep: dict[str, Any] = {}
    if rep_file.is_file():
        try:
            rep = json.loads(rep_file.read_text(encoding="utf-8"))
        except ValueError:
            rep = {}
    if not out.is_file():
        diags = _report_to_diagnostics(rep)
        raise IngestError(
            f"kicad-cli could not import {src.name}: " + "; ".join(d.message for d in diags[:3]),
            code="INGEST.IMPORT_FAILED",
        )
    stats = {k: int(v) for k, v in (rep.get("statistics") or {}).items() if isinstance(v, int)}
    return ImportResult(
        path=out,
        source_format=rep.get("source_format"),
        diagnostics=_report_to_diagnostics(rep),
        statistics=stats,
    )


def import_foreign(
    path: Path, fmt: str = "auto", *, out_dir: Path | None = None, tools: KiCadTools | None = None
) -> Path:
    return import_foreign_report(path, fmt, out_dir=out_dir, tools=tools).path


# ---------------------------------------------------------------------- pcbnew helper
def _helper(
    mode: str, args: list[Path], tools: KiCadTools | None, timeout: float
) -> dict[str, Any]:
    t = _tools(tools)
    py = t.require_python()
    res = t.run(py, [HELPER, mode, *args], timeout=timeout)
    last = [ln for ln in res.stdout.splitlines() if ln.strip().startswith("{")]
    if not last:
        raise ExternalToolFailed(
            "pcbnew helper printed no report",
            returncode=res.returncode,
            log_tail=res.stderr[-2000:],
        )
    out: dict[str, Any] = json.loads(last[-1])
    return out


def inspect_zones(
    path: Path, *, tools: KiCadTools | None = None, timeout: float = 300.0
) -> dict[str, Any]:
    """Zone fill report of a board (``areas`` per zone and copper layer, ``unfilled`` count)."""
    return _helper("inspect", [Path(path)], tools, timeout)


def refill_zones(
    path: Path, out: Path, *, tools: KiCadTools | None = None, timeout: float = 600.0
) -> dict[str, Any]:
    """Refill all zones with ``pcbnew.ZONE_FILLER`` in KiCad's Python and save to ``out``."""
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    rep = _helper("refill", [Path(path), Path(out)], tools, timeout)
    if not Path(out).is_file():
        raise ExternalToolFailed("pcbnew helper did not save the board", returncode=0)
    return rep


def prepare_read(
    path: Path,
    options: ReadOptions,
    *,
    tools: KiCadTools | None = None,
    work_dir: Path | None = None,
) -> tuple[Path, bool]:
    """Pre-step for ``ReadOptions.refill_zones``. Returns ``(path_to_read, refilled)``.

    ``never``: unchanged. ``if_unfilled``: refill only when some zone has no copper. ``always``:
    refill. A refilled copy is written to ``work_dir`` and the original is left untouched.
    """
    mode = options.refill_zones
    if mode == "never":
        return Path(path), False
    if mode == "if_unfilled" and inspect_zones(path, tools=tools)["unfilled"] == 0:
        return Path(path), False
    work = Path(work_dir) if work_dir is not None else Path(tempfile.mkdtemp(prefix="oa_refill_"))
    out = work / Path(path).name
    refill_zones(path, out, tools=tools)
    return out, True


def mark_refilled(board: Board) -> Board:
    """Set ``fill_state="refilled_by_tool"`` on every zone that carries fill copper."""
    zones = tuple(
        z.model_copy(update={"fill_state": "refilled_by_tool"})
        if z.fills and z.keepout is None
        else z
        for z in board.zones
    )
    return board.model_copy(update={"zones": zones})
