# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ingest reader protocol (interfaces.md section 3.2)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol, runtime_checkable

from oa_framework.errors import NoReader
from oa_model._base import OAModel
from oa_model.board import Board

if TYPE_CHECKING:
    from oa_framework.registry import Registry


class ReadOptions(OAModel):
    project_file: Path | None = None  # .kicad_pro next to .kicad_pcb (auto-detected if None)
    refill_zones: Literal["never", "if_unfilled", "always"] = "never"  # needs oa_kicad_bridge
    arc_tolerance: float = 1e-6
    strict: bool = False


@runtime_checkable
class Reader(Protocol):
    name: ClassVar[str]  # "kicad", "ipc2581", "altium"
    extensions: ClassVar[tuple[str, ...]]  # (".kicad_pcb",)

    @classmethod
    def can_read(cls, path: Path) -> bool:
        """Cheap sniff (first 4 KB)."""
        ...

    def read(self, path: Path, options: ReadOptions) -> Board: ...


def load(
    path: Path, options: ReadOptions | None = None, *, registry: Registry | None = None
) -> Board:
    """Pick a reader by ``can_read`` and read the file."""
    from oa_framework.registry import Registry as _Registry

    reg = registry if registry is not None else _Registry.load()
    opts = options if options is not None else ReadOptions()
    for name in sorted(reg.readers):
        reader_cls = reg.readers[name]
        if reader_cls.can_read(path):
            return reader_cls().read(path, opts)
    raise NoReader(
        f"No ingest reader recognizes {path.name}",
        path=str(path),
        hint=f"Installed readers: {', '.join(sorted(reg.readers)) or 'none'}.",
    )
