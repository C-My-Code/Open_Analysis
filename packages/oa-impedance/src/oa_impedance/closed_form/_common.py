# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared types for the closed-form models: result record and validity bookkeeping."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from scipy.special import ellipk, ellipkm1  # type: ignore[import-untyped,unused-ignore]

Validity = Literal["in_range", "extrapolated", "invalid"]

# A parameter more than this factor outside its published range makes the result "invalid".
INVALID_FACTOR = 3.0


@dataclass(frozen=True)
class RangeCheck:
    """One checked parameter against its published validity range."""

    name: str
    value: float
    lo: float
    hi: float

    @property
    def status(self) -> Validity:
        v = self.value
        if self.lo <= v <= self.hi:
            return "in_range"
        if (self.lo > 0 and v < self.lo / INVALID_FACTOR) or v > self.hi * INVALID_FACTOR:
            return "invalid"
        if self.lo <= 0 and v < self.lo - (self.hi - self.lo):
            return "invalid"
        return "extrapolated"

    def message(self) -> str:
        return f"{self.name}={self.value:.4g} outside published range [{self.lo:g}, {self.hi:g}]"


@dataclass(frozen=True)
class CFResult:
    """Result of one closed-form evaluation.

    ``z0``/``eeff`` are the single-line (or, for coupled models, the odd-mode-free) values;
    coupled models also fill the even/odd fields. ``validity`` is the worst status of ``checks``.
    """

    model: str
    z0: float | None = None
    eeff: float | None = None
    z0e: float | None = None
    z0o: float | None = None
    eeff_e: float | None = None
    eeff_o: float | None = None
    checks: tuple[RangeCheck, ...] = ()
    notes: tuple[str, ...] = ()
    legacy: bool = False
    details: dict[str, float] = field(default_factory=dict)

    @property
    def validity(self) -> Validity:
        status = {c.status for c in self.checks}
        if "invalid" in status:
            return "invalid"
        if "extrapolated" in status:
            return "extrapolated"
        return "in_range"

    @property
    def violations(self) -> tuple[str, ...]:
        return tuple(c.message() for c in self.checks if c.status != "in_range")

    @property
    def zdiff(self) -> float | None:
        return None if self.z0o is None else 2.0 * self.z0o

    @property
    def zcomm(self) -> float | None:
        return None if self.z0e is None else 0.5 * self.z0e


def kratio(k: float, kp: float | None = None) -> float:
    """K(k)/K(k') for modulus ``k``; pass ``kp`` (k' = sqrt(1-k^2)) when known more accurately."""
    m = k * k
    mp = (1.0 - m) if kp is None else kp * kp
    if m <= 0.5:
        return float(ellipk(m) / ellipkm1(m))
    return float(ellipkm1(mp) / ellipk(mp))
