# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Axis builder: ADR-0010 section 2 rules on top of :func:`smooth_axis`.

Rules applied: maximum cell ``lambda_min / N`` in the densest dielectric (``N`` = 10/20/30 for
coarse/medium/fine), the thirds rule at metal edges (1/3 inside, 2/3 outside), at least
``min_cells`` cells across every dielectric layer, a line on each copper face, grading at most
``grading`` away from fine regions, and snapping to feature positions.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np

from oa_mesh.rect._axis import AxisMesh, smooth_axis, third_rule_lines

C0 = 299792458.0  # [m/s]
QUALITY_N = {"coarse": 10, "medium": 20, "fine": 30}  # cells per wavelength [-]


def max_cell_size(f_max: float, eps_r_max: float, quality: str | int = "medium") -> float:
    """``lambda_min / N`` in the densest dielectric [m]; ``f_max`` [Hz], ``eps_r_max`` [-]."""
    n = QUALITY_N[quality] if isinstance(quality, str) else int(quality)
    return float(C0 / (f_max * eps_r_max**0.5) / n)


@dataclass(frozen=True)
class CopperFace:
    """A copper face at ``position`` [m]; ``outward`` is +1/-1 along the axis (away from metal)."""

    position: float
    outward: float = 1.0


@dataclass(frozen=True)
class AxisSpec:
    """Inputs of one axis; every position is in metres."""

    lo: float
    hi: float
    d_max: float  # [m] largest cell
    layers: tuple[float, ...] = ()  # dielectric interface positions
    copper_faces: tuple[CopperFace, ...] = ()
    features: tuple[tuple[float, float], ...] = ()  # (position, size) snaps, e.g. via diameters
    min_cells: int = 4  # per layer [-]
    grading: float = 1.3  # [-]


def build_axis(spec: AxisSpec) -> AxisMesh:
    """Mesh lines of one axis honouring the rules above. Raises ``ValueError`` on bad input."""
    if spec.hi <= spec.lo:
        raise ValueError("axis needs hi > lo")
    seeds: list[tuple[float, float]] = [(spec.lo, spec.d_max), (spec.hi, spec.d_max)]
    layer_pos = sorted({p for p in spec.layers if spec.lo < p < spec.hi})
    bounds = [spec.lo, *layer_pos, spec.hi]
    for a, b in itertools.pairwise(bounds):
        size = min(spec.d_max, (b - a) / spec.min_cells)
        seeds += [(a, size), (b, size), (0.5 * (a + b), size)]
    for cf in spec.copper_faces:
        size = min(spec.d_max, min(s + 0.3 * abs(cf.position - p) for p, s in seeds))
        outer, inner = third_rule_lines(cf.position, cf.outward, size)
        seeds += [(outer, size), (inner, size)]
    seeds += [(p, min(s, spec.d_max)) for p, s in spec.features]
    mesh = smooth_axis(seeds, spec.d_max, spec.grading)
    for a, b in itertools.pairwise(bounds):
        n = int(np.count_nonzero((mesh.lines[:-1] >= a - 1e-12) & (mesh.lines[1:] <= b + 1e-12)))
        if n < spec.min_cells:
            raise ValueError(f"layer [{a}, {b}] has {n} cells, below {spec.min_cells}")
    if mesh.max_cell > spec.d_max * (1 + 1e-9):
        raise ValueError("a cell exceeds d_max")
    return mesh
