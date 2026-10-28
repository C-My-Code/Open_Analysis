# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``ProfileResult`` with the summary of ai-integration.md section 4.2.

Node convention: ``s[i]`` is where node ``i`` starts and its values hold until ``s[i+1]``. Every
chain of runs ends with a marker node that repeats the last values; a node followed by a smaller
``s`` (a new chain, e.g. a branch) has zero length.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from pydantic import JsonValue

from oa_results.container import as_np
from oa_results.profile import ProfileResult

__all__ = ["ImpedanceProfileResult", "profile_stats"]


def profile_stats(
    s: Any, z: Any, target: float | None, tolerance: float | None
) -> dict[str, float | None]:
    """min / max / length-weighted mean Z and the length outside the tolerance band."""
    sv, zv = as_np(s), as_np(z)
    if sv.size == 0:
        return {"z_min": None, "z_max": None, "z_mean": None, "length_m": 0.0, "oot_m": None}
    ln = np.maximum(np.diff(sv, append=sv[-1]), 0.0)
    used = ln > 0
    zz = zv[used] if used.any() else zv
    total = float(ln.sum())
    mean = float((zv * ln).sum() / total) if total > 0 else float(zv.mean())
    oot: float | None = None
    if target:
        bad = np.abs(zv - target) > tolerance * target if tolerance else np.zeros_like(zv, bool)
        oot = float(ln[bad].sum())
    return {
        "z_min": float(zz.min()),
        "z_max": float(zz.max()),
        "z_mean": mean,
        "length_m": total,
        "oot_m": oot,
    }


class ImpedanceProfileResult(ProfileResult):
    """Profile of one net or pair, with min/max/mean, out-of-tolerance and worst-event summary."""

    def _z(self) -> Any:
        return next((q.values for q in self.quantities if q.name in ("Z0", "Zdiff")), None)

    def _worst_event(self) -> dict[str, JsonValue] | None:
        z = self._z()
        if z is None or not self.events:
            return None
        sv, zv = as_np(self.s), as_np(z)
        best: tuple[float, Any] | None = None
        for e in self.events:
            i = int(np.searchsorted(sv, e.s - 1e-12))
            i = min(i, sv.size - 1)
            if self.target:
                score = abs(float(zv[i]) - self.target) / self.target
            else:
                score = abs(float(zv[i] - zv[i - 1])) if i > 0 else 0.0
            if best is None or score > best[0]:
                best = (score, e)
        assert best is not None
        e = best[1]
        return {
            "kind": e.kind,
            "s_m": round(e.s, 9),
            "score": round(best[0], 4),
            "message": e.message[:160],
        }

    def _digest(self) -> str:
        net = self.net if isinstance(self.net, str) else "/".join(self.net)
        z = self._z()
        if z is None:
            return super()._digest()
        st = profile_stats(self.s, z, self.target, self.tolerance)
        text = f"{net}: Z {st['z_min']:.4g}..{st['z_max']:.4g} ohm (mean {st['z_mean']:.4g})"
        if self.target:
            text += f", target {self.target:g}+-{(self.tolerance or 0) * 100:g}%"
            text += f", {(st['oot_m'] or 0.0) * 1e3:.4g} mm out of tolerance"
        return text + f", {len(self.events)} event(s)."

    def _stats(self, max_items: int) -> dict[str, JsonValue]:
        z = self._z()
        out: dict[str, JsonValue] = {
            "net": self.net if isinstance(self.net, str) else list(self.net),
            "n_events": len(self.events),
            "target": self.target,
            "tolerance": self.tolerance,
        }
        if z is not None:
            st = profile_stats(self.s, z, self.target, self.tolerance)
            out.update({k: (None if v is None or math.isnan(v) else v) for k, v in st.items()})
        out["worst_event"] = self._worst_event()
        return out
