# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rectilinear mesh generation (ported from ``oa_em_openems.mesh``, WP-505): fixed lines with local target sizes, graded in between.

Each fixed line carries a *seed size* (the cell size wanted next to it). The local target size at
position ``x`` is ``min(d_max, min_i(seed_i + (grading - 1) * |x - x_i|))``, so cells grow at most
by ``grading`` per cell away from fine regions and never exceed ``d_max`` (lambda_min / N). Between
two fixed lines the number of cells is the smallest integer that keeps every cell below the target,
and the lines are spaced evenly in the integral of ``1 / target``. Size ratios between neighbours
are therefore at most ``grading`` in graded regions; across fixed lines (material interfaces, metal
edges) the ratio can reach about 1.5 because the geometry dictates the cell size there. The
achieved maximum is reported in the mesh statistics.

The "third rule" (metal edge one third of a cell from the inner line) is applied by the caller
through :func:`third_rule_lines`.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]

# Fixed lines closer than this (metres) are merged (1 nm).
MERGE_TOL = 1e-9


@dataclass(frozen=True)
class AxisMesh:
    """Mesh lines of one axis [m] and the fixed-line count that produced them."""

    lines: FloatArray
    n_fixed: int

    @property
    def cells(self) -> int:
        return int(self.lines.size - 1)

    @property
    def min_cell(self) -> float:
        return float(np.min(np.diff(self.lines)))

    @property
    def max_cell(self) -> float:
        return float(np.max(np.diff(self.lines)))

    @property
    def max_grading(self) -> float:
        d = np.diff(self.lines)
        if d.size < 2:
            return 1.0
        return float(np.max(np.maximum(d[1:] / d[:-1], d[:-1] / d[1:])))


def third_rule_lines(edge: float, outward: float, size: float) -> tuple[float, float]:
    """Lines for a metal edge at ``edge``: 2/3 of a cell outside, 1/3 inside.

    ``outward`` is the sign (+1 or -1) of the outward normal of the metal along the axis.
    """
    sign = 1.0 if outward >= 0 else -1.0
    return edge + sign * 2.0 * size / 3.0, edge - sign * size / 3.0


def merge_seeds(
    seeds: Iterable[tuple[float, float]], tol: float = MERGE_TOL
) -> list[tuple[float, float]]:
    """Sort seeds by position and merge positions within ``tol`` (the smaller size wins)."""
    out: list[tuple[float, float]] = []
    for pos, size in sorted(seeds):
        if out and pos - out[-1][0] <= tol:
            out[-1] = (out[-1][0], min(out[-1][1], size))
        else:
            out.append((pos, size))
    return out


def _target(
    x: FloatArray, seeds: list[tuple[float, float]], d_max: float, grading: float
) -> FloatArray:
    pos = np.array([s[0] for s in seeds])
    size = np.array([s[1] for s in seeds])
    t = np.min(size[None, :] + (grading - 1.0) * np.abs(x[:, None] - pos[None, :]), axis=1)
    out: FloatArray = np.minimum(t, d_max)
    return out


def smooth_axis(
    seeds: Iterable[tuple[float, float]],
    d_max: float,
    grading: float = 1.3,
    locked: Iterable[float] = (),
) -> AxisMesh:
    """Build the mesh lines of one axis from ``(position, seed size)`` fixed lines [m].

    Consecutive fixed lines that are both in ``locked`` bound exactly one cell (the caller already
    subdivided that interval, e.g. a dielectric into at least four cells).
    """
    locked_set = {round(x, 12) for x in locked}
    fixed = merge_seeds(seeds)
    if len(fixed) < 2:
        raise ValueError("an axis needs at least two fixed lines (the domain faces)")
    fixed = [(p, min(s, d_max)) for p, s in fixed]
    lines: list[float] = [fixed[0][0]]
    for (a, _), (b, _) in itertools.pairwise(fixed):
        if round(a, 12) in locked_set and round(b, 12) in locked_set:
            lines.append(b)
            continue
        n_sample = 400
        xs = np.linspace(a, b, n_sample)
        inv = 1.0 / _target(xs, fixed, d_max, grading)
        cum = np.concatenate([[0.0], np.cumsum(0.5 * (inv[1:] + inv[:-1]) * np.diff(xs))])
        n_cells = max(1, math.ceil(cum[-1] - 1e-9))
        marks = np.linspace(0.0, cum[-1], n_cells + 1)[1:-1]
        interior = np.interp(marks, cum, xs)
        lines.extend(float(v) for v in interior)
        lines.append(b)
    return AxisMesh(np.array(lines), len(fixed))


def round_lines(lines: FloatArray, unit: float, decimals: int = 3) -> FloatArray:
    """Lines in drawing units rounded to ``decimals`` (3 decimals of 1 um = 1 nm), deduplicated."""
    scaled = np.round(np.asarray(lines) / unit, decimals)
    return np.unique(scaled)


def estimate_timestep(dx: float, dy: float, dz: float, c_min: float = 299792458.0) -> float:
    """Courant limit with the smallest cell on each axis."""
    return 0.99 / (c_min * math.sqrt(1.0 / dx**2 + 1.0 / dy**2 + 1.0 / dz**2))
