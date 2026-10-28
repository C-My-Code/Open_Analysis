# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Progress parsers for streamed solver output: ``generic``, ``openems``, ``palace``.

A parser is fed one output line at a time and returns ``(fraction, message)`` when the line
carries progress, else None. Fractions never decrease within one parser instance and stay below
1.0 (the runner reports completion itself). The ``openems`` and ``palace`` patterns follow the
solvers' log formats as documented/observed and are best effort: a line they do not recognize is
simply ignored. WP-108 and WP-301 refine them against real runs.
"""

from __future__ import annotations

import re
from typing import Protocol

__all__ = ["PARSERS", "ProgressParser", "make_parser"]


class ProgressParser(Protocol):
    def feed(self, line: str) -> tuple[float, str] | None: ...


class _Monotonic:
    def __init__(self) -> None:
        self._last = 0.0

    def emit(self, fraction: float, message: str) -> tuple[float, str] | None:
        f = min(max(fraction, 0.0), 0.99)
        if f < self._last:
            return None
        self._last = f
        return f, message[:160]


class GenericParser(_Monotonic):
    _pct = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
    _ratio = re.compile(r"\b(\d+)\s*(?:/|of)\s*(\d+)\b")

    def feed(self, line: str) -> tuple[float, str] | None:
        text = line.strip()
        m = self._pct.search(text)
        if m and float(m.group(1)) <= 100.0:
            return self.emit(float(m.group(1)) / 100.0, text)
        m = self._ratio.search(text)
        if m:
            num, den = int(m.group(1)), int(m.group(2))
            if den > 0 and num <= den:
                return self.emit(num / den, text)
        return None


class OpenEmsParser(_Monotonic):
    """``[@ 12s] Timestep: 3200 || Speed: 35 MC/s || Energy: ~2.4e-18 (-33.3dB)``."""

    _total = re.compile(r"number of timesteps:\s*(\d+)", re.IGNORECASE)
    _step = re.compile(r"Timestep:\s*(\d+)(?:\s*\(of\s*(\d+)\)|\s*/\s*(\d+))?")
    _energy = re.compile(r"\((-?\d+(?:\.\d+)?)\s*dB\)")

    def __init__(self, end_criterion_db: float = 50.0) -> None:
        super().__init__()
        self.total: int | None = None
        self.end_db = end_criterion_db

    def feed(self, line: str) -> tuple[float, str] | None:
        m = self._total.search(line)
        if m:
            self.total = int(m.group(1)) or None
            return None
        m = self._step.search(line)
        if not m:
            return None
        step = int(m.group(1))
        total = int(m.group(2) or m.group(3) or 0) or self.total
        if total:
            return self.emit(step / total, f"timestep {step}/{total}")
        e = self._energy.search(line)
        if e:
            return self.emit(
                -float(e.group(1)) / self.end_db, f"timestep {step}, energy {e.group(1)} dB"
            )
        return None


class PalaceParser(_Monotonic):
    """Frequency / sweep / iteration counters such as ``Frequency 3/10`` or ``Step 12 of 40``."""

    _count = re.compile(
        r"\b(?:Frequency|Freq|Sweep|Step|Iteration|It|Port)\s*[:#]?\s*(\d+)\s*(?:/|of)\s*(\d+)",
        re.IGNORECASE,
    )

    def feed(self, line: str) -> tuple[float, str] | None:
        m = self._count.search(line)
        if m and int(m.group(2)) > 0 and int(m.group(1)) <= int(m.group(2)):
            return self.emit(int(m.group(1)) / int(m.group(2)), line.strip())
        return None


PARSERS: dict[str, type[GenericParser] | type[OpenEmsParser] | type[PalaceParser]] = {
    "generic": GenericParser,
    "openems": OpenEmsParser,
    "palace": PalaceParser,
}


def make_parser(name: str | None) -> ProgressParser | None:
    if name is None:
        return None
    try:
        return PARSERS[name]()
    except KeyError:
        raise ValueError(f"unknown progress parser {name!r}; known: {sorted(PARSERS)}") from None
