# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Altium ``.PcbDoc`` reader: ``kicad-cli pcb import`` followed by the KiCad reader.

The bridge sits next to ``oa_ingest_kicad`` in the layering, so it does not import it: the
``kicad`` reader is looked up at run time in the ``open_analysis.ingest`` entry-point group.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, ClassVar

from oa_kicad_bridge.bridge import (
    import_foreign_report,
    mark_refilled,
    prepare_read,
)
from oa_kicad_bridge.tools import KiCadTools
from oa_model.board import Board
from oa_model.errors import IngestError, ToolMissing
from oa_model.setup import ReadOptions

_OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
READER_NAME = "oa-kicad-bridge 0.1.0"


def load_kicad_reader() -> Any:
    """The registered ``kicad`` reader class (resolved lazily; raises if not installed)."""
    for ep in entry_points(group="open_analysis.ingest"):
        if ep.name == "kicad":
            return ep.load()
    raise IngestError(
        "the 'kicad' ingest reader (oa-ingest-kicad) is not installed",
        code="INGEST.NO_KICAD_READER",
    )


class AltiumReader:
    """Reader for Altium Designer ``.PcbDoc`` (entry point ``altium``)."""

    name: ClassVar[str] = "altium"
    extensions: ClassVar[tuple[str, ...]] = (".PcbDoc", ".pcbdoc")

    def __init__(self, tools: KiCadTools | None = None) -> None:
        self._tools = tools

    @classmethod
    def can_read(cls, path: Path) -> bool:
        p = Path(path)
        if p.suffix.lower() != ".pcbdoc":
            return False
        try:
            with p.open("rb") as fh:
                return fh.read(8) == _OLE_MAGIC
        except OSError:
            return False

    def read(self, path: Path, options: ReadOptions | None = None) -> Board:
        opts = options if options is not None else ReadOptions()
        src = Path(path)
        tools = self._tools if self._tools is not None else KiCadTools.discover()
        try:
            tools.require_cli()
        except ToolMissing as exc:
            raise IngestError(
                f"reading {src.name} needs kicad-cli: {exc.hint}", code="INGEST.TOOL_MISSING"
            ) from exc
        work = Path(tempfile.mkdtemp(prefix="oa_altium_"))
        try:
            imp = import_foreign_report(src, "altium", out_dir=work, tools=tools)
            target, refilled = prepare_read(imp.path, opts, tools=tools, work_dir=work / "refill")
            reader_cls = load_kicad_reader()
            board: Board = reader_cls().read(target, opts)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        if refilled:
            board = mark_refilled(board)
        meta = board.meta.model_copy(
            update={
                "source_tool": "altium",
                "source_file": src.name,
                "source_hash": hashlib.sha256(src.read_bytes()).hexdigest(),
                "reader": f"{READER_NAME} via kicad-cli {tools.version() or '?'}",
            }
        )
        return board.model_copy(
            update={"meta": meta, "diagnostics": (*board.diagnostics, *imp.diagnostics)}
        )
