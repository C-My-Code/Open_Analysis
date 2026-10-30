# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Analytic influence coefficients of flat constant-density panels (2D, free space, eps0 = 1).

For a panel carrying uniform line charge sigma~ (charge / eps0):

- potential:  phi = -(1/2 pi) * integral ln r dl                       -> ``P``
- field:      E_t = (1/2 pi) ln(r1/r2),  E_n = (1/2 pi) * subtended angle -> ``D`` (E . n_target)

All integrals are closed form (no quadrature), including the self term (``P`` exact, ``D`` = 0 as
the principal value of the normal field on the panel itself).
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

F64 = npt.NDArray[np.float64]

__all__ = ["influence", "influence_blocks"]

_INV_2PI = 1.0 / (2.0 * np.pi)


def influence(
    mid: F64, normal: F64, a: F64, b: F64, tangent: F64, nrm: F64, length: F64
) -> tuple[F64, F64]:
    """``P[i, j]`` and ``D[i, j]``: potential and normal field at ``mid[i]`` (normal ``normal[i]``)
    from unit density on panel ``j`` (``a[j]`` -> ``b[j]``)."""
    del b
    rx = mid[:, None, 0] - a[None, :, 0]
    ry = mid[:, None, 1] - a[None, :, 1]
    x0 = rx * tangent[None, :, 0] + ry * tangent[None, :, 1]
    y0 = rx * nrm[None, :, 0] + ry * nrm[None, :, 1]
    s1 = -x0
    s2 = length[None, :] - x0
    y2 = y0 * y0
    r1sq = s1 * s1 + y2
    r2sq = s2 * s2 + y2
    tiny = 1e-300
    l1 = np.log(np.maximum(r1sq, tiny))
    l2 = np.log(np.maximum(r2sq, tiny))
    ay = np.abs(y0)
    f2 = 0.5 * s2 * l2 - s2 + ay * np.arctan2(s2, ay)
    f1 = 0.5 * s1 * l1 - s1 + ay * np.arctan2(s1, ay)
    p = -_INV_2PI * (f2 - f1)
    ex = _INV_2PI * 0.5 * (l1 - l2)
    ey = _INV_2PI * np.arctan2(y0 * (s2 - s1), y2 + s1 * s2)
    d = ex * (
        tangent[None, :, 0] * normal[:, None, 0] + tangent[None, :, 1] * normal[:, None, 1]
    ) + ey * (nrm[None, :, 0] * normal[:, None, 0] + nrm[None, :, 1] * normal[:, None, 1])
    return p, d


_BLOCK_ENTRIES = 1 << 19  # about 4 MB per float64 temporary; the kernel holds ~20 of them


def influence_blocks(
    mid: F64,
    normal: F64,
    a: F64,
    b: F64,
    tangent: F64,
    nrm: F64,
    length: F64,
    p_out: F64 | None = None,
    d_out: F64 | None = None,
    *,
    same: bool = False,
) -> tuple[F64, F64]:
    """``influence`` evaluated in row blocks, so the kernel temporaries stay a few tens of MB
    instead of ~20 copies of the full matrix. Writes into ``p_out`` / ``d_out`` when given. The
    diagonal (panel on itself) has ``D = 0`` when ``same`` (rows and columns are
    the same panels)."""
    n_r, n_c = mid.shape[0], a.shape[0]
    p = p_out if p_out is not None else np.empty((n_r, n_c))
    d = d_out if d_out is not None else np.empty((n_r, n_c))
    rows = max(8, _BLOCK_ENTRIES // max(n_c, 1))
    for r0 in range(0, n_r, rows):
        r1 = min(n_r, r0 + rows)
        pb, db = influence(mid[r0:r1], normal[r0:r1], a, b, tangent, nrm, length)
        p[r0:r1] = pb
        d[r0:r1] = db
    if same:
        idx = np.arange(n_r)
        d[idx, idx] = 0.0
    return p, d
