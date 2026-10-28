# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""IPC-2141 "simple" formulas. LEGACY: for comparison with fab quotes only.

IPC-2141A itself recommends field solvers; these formulas err by 2-10 % and the coupled forms
ignore er and W in the coupling term (docs/research/02 sec. 1.4). Every result has
``legacy=True`` and a warning note. Never selected by ``method="auto"``.
"""

from __future__ import annotations

import math

from ._common import CFResult, RangeCheck

__all__ = ["ipc2141_microstrip", "ipc2141_stripline"]

_WARN = "IPC-2141 legacy formula: errors of 2-10 %; use only for comparison with fab quotes"


def ipc2141_microstrip(w: float, h: float, t: float, er: float, s: float | None = None) -> CFResult:
    z0 = 87.0 / math.sqrt(er + 1.41) * math.log(5.98 * h / (0.8 * w + t))
    z0o = None
    if s is not None:
        zdiff = 2.0 * z0 * (1.0 - 0.48 * math.exp(-0.96 * s / h))
        z0o = zdiff / 2.0
    return CFResult(
        model="ipc2141_microstrip",
        z0=z0,
        z0o=z0o,
        checks=(RangeCheck("W/h", w / h, 0.1, 2.0), RangeCheck("er", er, 1.0, 15.0)),
        notes=(_WARN,),
        legacy=True,
    )


def ipc2141_stripline(w: float, b: float, t: float, er: float, s: float | None = None) -> CFResult:
    z0 = 60.0 / math.sqrt(er) * math.log(1.9 * b / (0.8 * w + t))
    z0o = None
    if s is not None:
        z0o = z0 * (1.0 - 0.347 * math.exp(-2.9 * s / b))
    return CFResult(
        model="ipc2141_stripline",
        z0=z0,
        z0o=z0o,
        checks=(
            RangeCheck("W/(b-t)", w / (b - t), 0.0, 0.35),
            RangeCheck("t/b", t / b, 0.0, 0.25),
        ),
        notes=(_WARN,),
        legacy=True,
    )
