# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bridge tests with kicad-cli and pcbnew mocked (no KiCad needed)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

import oa_kicad_bridge.altium as altium_mod
import oa_kicad_bridge.bridge as bridge
from oa_kicad_bridge import AltiumReader, KiCadTools
from oa_kicad_bridge.tools import RunResult
from oa_model.board import Board
from oa_model.copper import Zone, ZoneFill
from oa_model.errors import ExternalToolFailed, IngestError, ToolMissing
from oa_model.geometry import Polygon
from oa_model.setup import ReadOptions
from oa_testkit.builders import microstrip_board


class FakeTools(KiCadTools):
    """Records calls; `handler(program_name, args)` plays the part of kicad-cli / python."""

    def __init__(self, tmp: Path, handler: Any) -> None:
        cli = tmp / "kicad-cli"
        py = tmp / "python"
        cli.write_text("")
        py.write_text("")
        super().__init__(cli, py)
        self.handler = handler
        self.calls: list[list[str]] = []

    def run(
        self, program: Path, args: Any, *, timeout: float = 600.0, check: bool = True
    ) -> RunResult:
        a = [str(x) for x in args]
        self.calls.append([Path(program).name, *a])
        return self.handler(Path(program).name, a)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------- discovery
def test_discover_prefers_explicit_then_env_then_toml(tmp_path: Path) -> None:
    a, b, c = (tmp_path / n for n in ("a", "b", "c"))
    for p in (a, b, c):
        p.write_text("")
    toml = tmp_path / "tools.toml"
    toml.write_text(f'[tools.kicad-cli]\npath = "{c.as_posix()}"\n')
    env = {"OA_TOOLS_TOML": str(toml)}
    assert KiCadTools.discover(env=env).cli == c
    assert KiCadTools.discover(env={**env, "KICAD_CLI": str(b)}).cli == b
    assert KiCadTools.discover(cli=a, env={**env, "KICAD_CLI": str(b)}).cli == a


def test_discover_from_kicad_root(tmp_path: Path) -> None:
    root = tmp_path / "kicad"
    (root / "bin").mkdir(parents=True)
    exe = root / "bin" / ("kicad-cli.exe" if sys.platform == "win32" else "kicad-cli")
    exe.write_text("")
    assert KiCadTools.discover(env={"KICAD_ROOT": str(root)}).cli == exe


def test_missing_tool_raises_tool_missing(tmp_path: Path) -> None:
    t = KiCadTools(tmp_path / "nope", None)
    with pytest.raises(ToolMissing) as ei:
        t.require_cli()
    assert ei.value.code == "TOOL_MISSING" and "KICAD_CLI" in ei.value.hint
    with pytest.raises(ToolMissing):
        t.require_python()


def test_real_subprocess_failure_is_external_tool_failed(tmp_path: Path) -> None:
    t = KiCadTools(Path(sys.executable), Path(sys.executable))
    with pytest.raises(ExternalToolFailed) as ei:
        t.run(Path(sys.executable), ["-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"])
    assert ei.value.returncode == 3 and "boom" in ei.value.log_tail
    with pytest.raises(ExternalToolFailed):
        t.run(Path(sys.executable), ["-c", "import time; time.sleep(5)"], timeout=0.3)


# ---------------------------------------------------------------------- import / drc
def _import_handler(report: dict[str, Any], *, write_output: bool = True) -> Any:
    def handler(prog: str, args: list[str]) -> RunResult:
        assert args[:2] == ["pcb", "import"]
        out = Path(args[args.index("-o") + 1])
        rep = Path(args[args.index("--report-file") + 1])
        assert args[args.index("--format") + 1] == "altium"
        if write_output:
            out.write_text("(kicad_pcb)")
        rep.write_text(json.dumps(report))
        return RunResult(0, "", "")

    return handler


def test_import_foreign_parses_report_into_diagnostics(tmp_path: Path) -> None:
    rep = {
        "errors": [],
        "warnings": ["polygon outline simplified", {"message": "region ignored"}],
        "layer_mapping": {
            "Mechanical 1": {"kicad_layer": "User.1", "method": "manual"},
            "Top Layer": {"kicad_layer": "F.Cu", "method": "auto"},
        },
        "source_format": "Altium Designer",
        "statistics": {"tracks": 5, "vias": 2},
    }
    t = FakeTools(tmp_path, _import_handler(rep))
    res = bridge.import_foreign_report(
        tmp_path / "x.PcbDoc", "altium", out_dir=tmp_path / "o", tools=t
    )
    assert res.path.name == "x.kicad_pcb" and res.path.is_file()
    assert res.statistics == {"tracks": 5, "vias": 2}
    codes = [(d.severity, d.code) for d in res.diagnostics]
    assert codes.count(("warning", "INGEST.IMPORT_WARNING")) == 2
    assert ("info", "INGEST.IMPORT_LAYER_MAPPING") in codes
    assert bridge.import_foreign(
        tmp_path / "x.PcbDoc", "altium", out_dir=tmp_path / "o2", tools=t
    ).is_file()


def test_import_foreign_failure_and_bad_format(tmp_path: Path) -> None:
    t = FakeTools(
        tmp_path, _import_handler({"errors": ["unsupported version"]}, write_output=False)
    )
    with pytest.raises(IngestError) as ei:
        bridge.import_foreign(tmp_path / "x.PcbDoc", "altium", tools=t)
    assert ei.value.code == "INGEST.IMPORT_FAILED" and "unsupported version" in ei.value.message
    with pytest.raises(IngestError):
        bridge.import_foreign(tmp_path / "x.PcbDoc", "nonsense", tools=t)


def test_drc_parses_json(tmp_path: Path) -> None:
    def handler(prog: str, args: list[str]) -> RunResult:
        Path(args[args.index("-o") + 1]).write_text(
            json.dumps(
                {
                    "kicad_version": "10.0.5",
                    "coordinate_units": "mm",
                    "violations": [
                        {
                            "type": "clearance",
                            "severity": "error",
                            "description": "Clearance violation",
                            "items": [
                                {"description": "Track", "pos": {"x": 1.0, "y": 2.0}, "uuid": "u"}
                            ],
                        },
                        {
                            "type": "via_dangling",
                            "severity": "warning",
                            "description": "Via",
                            "items": [],
                        },
                    ],
                    "unconnected_items": [],
                    "schematic_parity": [],
                }
            )
        )
        return RunResult(0, "", "")

    t = FakeTools(tmp_path, handler)
    rep = bridge.drc(tmp_path / "b.kicad_pcb", tools=t, refill=True)
    assert (rep.count("error"), rep.count("warning")) == (1, 1)
    assert rep.violations[0].items[0].x_mm == 1.0
    assert "--refill-zones" in t.calls[0] and "--severity-all" in t.calls[0]


# ---------------------------------------------------------------------- refill pre-step
def _helper_handler(unfilled: int, refilled: list[Path]) -> Any:
    def handler(prog: str, args: list[str]) -> RunResult:
        mode = args[1]
        if mode == "inspect":
            doc = {"zones": 2, "unfilled": unfilled, "areas": []}
        else:
            Path(args[3]).write_text("(kicad_pcb)")
            refilled.append(Path(args[3]))
            doc = {"zones": 2, "unfilled": 0, "areas": []}
        return RunResult(0, "noise\n" + json.dumps(doc) + "\n", "")

    return handler


@pytest.mark.parametrize(
    ("mode", "unfilled", "expect_refill"),
    [("never", 1, False), ("if_unfilled", 0, False), ("if_unfilled", 2, True), ("always", 0, True)],
)
def test_prepare_read_honours_refill_option(
    tmp_path: Path, mode: str, unfilled: int, expect_refill: bool
) -> None:
    done: list[Path] = []
    t = FakeTools(tmp_path, _helper_handler(unfilled, done))
    src = tmp_path / "b.kicad_pcb"
    src.write_text("(kicad_pcb)")
    out, refilled = bridge.prepare_read(
        src,
        ReadOptions(refill_zones=mode),
        tools=t,
        work_dir=tmp_path / "w",  # type: ignore[arg-type]
    )
    assert refilled is expect_refill
    assert (out != src) is expect_refill
    assert bool(done) is expect_refill
    assert bridge.HELPER.name == "pcbnew_helper.py" and bridge.HELPER.is_file()


def test_mark_refilled_only_touches_filled_zones() -> None:
    b = microstrip_board()
    poly = Polygon(exterior=[(0, 0), (1e-3, 0), (1e-3, 1e-3)])  # type: ignore[arg-type]
    z = Zone(
        id="z1",
        net=None,
        layers=("L2",),
        outline=poly,
        fills=(ZoneFill(layer="L2", polygons=(poly,)),),
        fill_state="filled",
    )
    empty = Zone(id="z2", net=None, layers=("L2",), outline=poly, fill_state="unfilled")
    b2 = bridge.mark_refilled(b.model_copy(update={"zones": (z, empty)}))
    assert [x.fill_state for x in b2.zones] == ["refilled_by_tool", "unfilled"]


# ---------------------------------------------------------------------- AltiumReader (mocked)
class _FakeKiCadReader:
    seen: list[Path] = []

    def read(self, path: Path, options: ReadOptions) -> Board:
        _FakeKiCadReader.seen.append(path)
        assert path.is_file()
        return microstrip_board()


def test_altium_reader_delegates_and_relabels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rep = {"warnings": ["w"], "statistics": {"tracks": 1}, "source_format": "Altium Designer"}

    def handler(prog: str, args: list[str]) -> RunResult:
        if args[0] == "version":
            return RunResult(0, "10.0.5\n", "")
        return _import_handler(rep)(prog, args)

    monkeypatch.setattr(altium_mod, "load_kicad_reader", lambda: _FakeKiCadReader)
    t = FakeTools(tmp_path, handler)
    src = tmp_path / "b.PcbDoc"
    src.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"payload")
    assert AltiumReader.can_read(src)
    assert not AltiumReader.can_read(tmp_path / "b.kicad_pcb")
    board = AltiumReader(t).read(src, ReadOptions())
    assert board.meta.source_tool == "altium" and board.meta.source_file == "b.PcbDoc"
    assert board.meta.source_hash and len(board.meta.source_hash) == 64
    assert any(d.code == "INGEST.IMPORT_WARNING" for d in board.diagnostics)
    assert _FakeKiCadReader.seen and not _FakeKiCadReader.seen[0].exists()  # temp dir cleaned up


def test_altium_reader_without_kicad_cli_or_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "b.PcbDoc"
    src.write_bytes(b"x")
    with pytest.raises(IngestError) as ei:
        AltiumReader(KiCadTools(None, None)).read(src)
    assert ei.value.code == "INGEST.TOOL_MISSING"
    t = FakeTools(tmp_path, _import_handler({}))
    monkeypatch.setattr(altium_mod, "entry_points", lambda group: [])
    with pytest.raises(IngestError) as ei2:
        AltiumReader(t).read(src)
    assert ei2.value.code == "INGEST.NO_KICAD_READER"


def test_pcbnew_never_imported() -> None:
    assert "pcbnew" not in sys.modules
