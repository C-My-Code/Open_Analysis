# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="no-any-return,unused-ignore,import-untyped,attr-defined"
"""Optional compiled clipping kernels (``oa-native``, ``OA_NATIVE=0|1|auto``), WP-509b.

Every kernel has a shapely/GEOS oracle in the calling module; ``kernels()`` returns ``None``
when the compiled module is disabled or missing, and callers use the oracle.
"""

from __future__ import annotations

import os
from types import ModuleType

import numpy as np
import numpy.typing as npt
import shapely

_NEEDED = ("cut_intervals", "rect_cover_edge")


_KCACHE: dict[tuple[str, str], ModuleType | None] = {}


def kernels() -> ModuleType | None:
    # called per cut batch (tens of thousands of times per board): cache by the two env switches
    env = (os.environ.get("OA_NATIVE_CLIP", "auto"), os.environ.get("OA_NATIVE", "auto"))
    try:
        return _KCACHE[env]
    except KeyError:
        pass
    mod = _kernels(env)
    _KCACHE[env] = mod
    return mod


def _kernels(env: tuple[str, str]) -> ModuleType | None:
    if env[0].strip() == "0":
        return None  # clipping kernels off (GEOS oracle), whatever OA_NATIVE says
    try:
        from oa_native import get
    except ImportError:
        if env[1].strip() == "1":
            raise
        return None
    mod = get()
    if mod is None or not all(hasattr(mod, n) for n in _NEEDED):
        return None
    return mod


class RingSet:
    """Flattened rings of polygonal geometries for the native kernels.

    ``xy`` (m, 2) ring vertices, ``ring_start`` (r + 1,) and ``geom_start`` (g + 1,) offsets.
    """

    __slots__ = ("geom_start", "ring_start", "xy")

    def __init__(self, geoms: npt.NDArray[np.object_]) -> None:
        g = len(geoms)
        parts, pg = shapely.get_parts(geoms, return_index=True)
        polys = shapely.get_type_id(parts) == 3
        parts, pg = parts[polys], pg[polys]
        rings, rp = shapely.get_rings(parts, return_index=True)
        rg = pg[rp]
        xy, ri = shapely.get_coordinates(rings, return_index=True)
        self.xy = np.ascontiguousarray(xy, dtype=np.float64).reshape(-1, 2)
        self.ring_start = np.concatenate(
            ([0], np.cumsum(np.bincount(ri, minlength=len(rings))))
        ).astype(np.int64)
        self.geom_start = np.concatenate(([0], np.cumsum(np.bincount(rg, minlength=g)))).astype(
            np.int64
        )
