# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Triangle quality metrics (min angle, aspect ratio, area)."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from oa_mesh.types import MeshQuality

__all__ = ["quality_report", "triangle_areas"]


def triangle_areas(
    nodes: npt.NDArray[np.float64], tris: npt.NDArray[np.int64]
) -> npt.NDArray[np.float64]:
    """Signed areas (positive = counter-clockwise) of the corner triangles [m^2]."""
    p = nodes[tris[:, :3]]
    d1 = p[:, 1] - p[:, 0]
    d2 = p[:, 2] - p[:, 0]
    return np.asarray(0.5 * (d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]), dtype=np.float64)


def quality_report(nodes: npt.NDArray[np.float64], tris: npt.NDArray[np.int64]) -> MeshQuality:
    """Quality of the corner triangles. Aspect = circumradius / (2 inradius) (1 = equilateral)."""
    if len(tris) == 0:
        return MeshQuality(
            n_nodes=len(nodes),
            n_triangles=0,
            min_angle_deg=0.0,
            max_angle_deg=0.0,
            max_aspect=1e300,
            min_edge=0.0,
            max_edge=0.0,
            area=0.0,
        )
    p = nodes[tris[:, :3]]
    e = np.stack([p[:, 2] - p[:, 1], p[:, 0] - p[:, 2], p[:, 1] - p[:, 0]], axis=1)
    ln = np.hypot(e[..., 0], e[..., 1])  # (M, 3), edge i opposite vertex i
    area = np.abs(triangle_areas(nodes, tris))
    a, b, c = ln[:, 0], ln[:, 1], ln[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        cosv = np.stack(
            [
                (b**2 + c**2 - a**2) / (2 * b * c),
                (a**2 + c**2 - b**2) / (2 * a * c),
                (a**2 + b**2 - c**2) / (2 * a * b),
            ],
            axis=1,
        )
        ang = np.degrees(np.arccos(np.clip(cosv, -1.0, 1.0)))
        s = 0.5 * (a + b + c)
        inr = area / s
        circ = a * b * c / (4 * area)
        aspect = circ / (2 * inr)
    aspect = np.where(np.isfinite(aspect), aspect, 1e300)
    ang = np.where(np.isfinite(ang), ang, 0.0)
    return MeshQuality(
        n_nodes=len(nodes),
        n_triangles=len(tris),
        min_angle_deg=float(ang.min()),
        max_angle_deg=float(ang.max()),
        max_aspect=float(aspect.max()),
        min_edge=float(ln.min()),
        max_edge=float(ln.max()),
        area=float(area.sum()),
    )
