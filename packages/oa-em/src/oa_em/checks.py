# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Convergence checks, the port-impedance sanity check and result summaries."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from oa_em.problem import PortInfo
from oa_model.common import Diagnostic
from oa_results.tables import TableResult

__all__ = [
    "closed_form_z0",
    "energy_convergence",
    "measured_z0",
    "mesh_delta",
    "s_points_text",
    "z0_check",
]


def energy_convergence(
    table: TableResult | None, target_db: float, margin_db: float
) -> tuple[bool | None, float | None]:
    """Energy end criterion: the last logged energy of every excitation reached the target.

    Returns ``(converged, worst_final_energy_db)``; ``(None, None)`` when the adapter logged
    nothing (not applicable)."""
    if table is None or not table.inline:
        return None, None
    last: dict[str, float] = {}
    for run, _step, db in table.inline:
        last[str(run)] = float(db)  # type: ignore[arg-type]
    worst = max(last.values())
    return bool(worst <= target_db + margin_db), worst


def mesh_delta(s_a: np.ndarray, s_b: np.ndarray) -> float:
    """Max over frequency and ports of |S_a - S_b|, the mesh-refinement criterion."""
    if s_a.shape != s_b.shape:
        return math.inf
    return float(np.max(np.abs(s_a - s_b)))


def closed_form_z0(ctx: Any, geo: Any, info: PortInfo) -> float | None:
    """Closed-form line impedance at a wave port through the ``impedance`` service, or None."""
    try:
        svc = ctx.services()
        if not svc.has("impedance") or info.seg_id is None:
            return None
        net = info.nets[0]
        seg = next(s for s in geo.segments(net) if s.id == info.seg_id)
        px, py = info.position
        a, b = seg.seg.start, seg.seg.end
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        s = ((px - a[0]) * dx + (py - a[1]) * dy) / length
        s = min(max(s, 0.1 * length), 0.9 * length)  # stay on the segment, away from the end
        xs = geo.cross_section_at(seg, s, include_aggressors=False)
        res = svc.get("impedance").quick(xs)
        p = res.params
        if p.z0 is not None:
            return float(np.asarray(p.z0.array).ravel()[0])
        return float(np.abs(np.asarray(p.zc_modal.array)).ravel()[0])
    except Exception:
        return None


def measured_z0(table: TableResult | None, em_name: str) -> float | None:
    """Median |Z| of one port from the adapter's port-impedance table."""
    if table is None or not table.inline:
        return None
    names = [c.name for c in table.columns]
    if f"{em_name}.z_re" not in names:
        return None
    i_re, i_im = names.index(f"{em_name}.z_re"), names.index(f"{em_name}.z_im")
    z = np.array([complex(float(r[i_re]), float(r[i_im])) for r in table.inline])  # type: ignore[arg-type]
    z = z[np.isfinite(z)]
    return float(np.median(np.abs(z))) if z.size else None


def z0_check(
    info: PortInfo, z_ref: float | None, z_port: float | None, tol: float, where: str
) -> Diagnostic | None:
    """Warn when the port impedance differs from the closed-form line by more than ``tol``."""
    if z_ref is None or z_port is None or z_ref <= 0:
        return None
    rel = abs(z_port - z_ref) / z_ref
    if rel <= tol:
        return None
    return Diagnostic(
        severity="warning",
        code="EM.PORT_Z0_MISMATCH",
        message=f"port {info.em_name}: {where} impedance {z_port:.1f} ohm differs "
        f"{100 * rel:.0f} % from the closed-form line impedance {z_ref:.1f} ohm",
        item=info.em_name,
        hint="Check the port reference impedance, the line width and the stackup; set "
        "z0 to None to renormalise to the line impedance.",
    )


def s_points_text(freqs: np.ndarray, s: np.ndarray) -> str:
    """|S21| and |S11| [dB] at f_max and three log-spaced points (ai-integration 4.2)."""
    n = s.shape[1]
    f_pos = freqs[freqs > 0]
    if f_pos.size == 0:
        return ""
    pts = np.unique(np.concatenate([np.geomspace(f_pos[0], f_pos[-1], 4)[1:-1], [f_pos[-1]]]))
    parts = []
    for f in pts:
        k = int(np.argmin(np.abs(freqs - f)))
        s11 = 20 * math.log10(max(abs(s[k, 0, 0]), 1e-12))
        txt = f"{freqs[k] / 1e9:.3g} GHz S11 {s11:.1f} dB"
        if n > 1:
            s21 = 20 * math.log10(max(abs(s[k, 1, 0]), 1e-12))
            txt += f", S21 {s21:.2f} dB"
        parts.append(txt)
    return "; ".join(parts)
