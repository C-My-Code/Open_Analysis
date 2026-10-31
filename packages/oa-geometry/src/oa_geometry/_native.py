# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="no-any-return,unused-ignore,import-untyped,attr-defined"
"""Optional compiled clipping kernels (``oa-native``, ``OA_NATIVE=0|1|auto``), WP-509b.

Every kernel has a shapely/GEOS oracle in the calling module; ``kernels()`` returns ``None``
when the compiled module is disabled or missing, and callers use the oracle.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from types import ModuleType

import numpy as np
import numpy.typing as npt
import shapely
from shapely import STRtree

_NEEDED = ("cut_intervals", "rect_cover_edge")
_log = logging.getLogger("oa_geometry")


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
        if env[1].strip() != "0":
            _log.warning(
                "oa_geometry: oa-native is not installed; clipping runs on the GEOS fallback"
            )
        return None
    mod = get()  # warns once itself when the module is missing or stale
    if mod is None:
        return None
    missing = [n for n in _NEEDED if not hasattr(mod, n)]
    if missing:
        _log.warning(
            "oa_geometry: oa_native has no %s (stale build); clipping runs on the GEOS fallback",
            ", ".join(missing),
        )
        return None
    return mod


class RingSet:
    """Flattened rings of polygonal geometries for the native kernels.

    ``xy`` (m, 2) ring vertices, ``ring_start`` (r + 1,) and ``geom_start`` (g + 1,) offsets.
    """

    __slots__ = ("_tree", "geom_start", "ring_start", "xy")

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
        self._tree: tuple[STRtree, npt.NDArray[np.int64], npt.NDArray[np.int64]] | None = None

    def _ring_tree(self) -> tuple[STRtree, npt.NDArray[np.int64], npt.NDArray[np.int64]]:
        """STRtree of the ring bounding boxes, the geometry of each ring, ring sizes."""
        if self._tree is None:
            nv = np.diff(self.ring_start)
            lo = np.full((len(nv), 2), np.inf)
            hi = np.full((len(nv), 2), -np.inf)
            ne = np.flatnonzero(nv > 0)
            if len(ne):
                starts = self.ring_start[ne]
                lo[ne] = np.minimum.reduceat(self.xy, starts, axis=0)
                hi[ne] = np.maximum.reduceat(self.xy, starts, axis=0)
            boxes = np.empty(len(nv), dtype=object)
            boxes[:] = None
            boxes[ne] = shapely.box(lo[ne, 0], lo[ne, 1], hi[ne, 0], hi[ne, 1])
            ring_geom = np.repeat(np.arange(len(self.geom_start) - 1), np.diff(self.geom_start))
            self._tree = (STRtree(boxes), ring_geom.astype(np.int64), nv.astype(np.int64))
        return self._tree

    def near(
        self,
        geom_of: npt.NDArray[np.int64],
        box: npt.NDArray[np.float64],
        max_vertices: int = 4_000_000,
    ) -> Iterator[tuple[npt.NDArray[np.intp], RingSet]]:
        """Per query ``i``: the rings of geometry ``geom_of[i]`` whose bounding box meets
        ``box[i]`` (``x0, y0, x1, y1``), as a ring set with one geometry per query (WP-520).

        A kernel that only looks at ring edges near ``box`` and at the even-odd parity of a
        point inside ``box`` gives the same answer on this subset as on the whole geometry: a
        ring whose bounding box misses ``box`` has no edge near it and crosses a ray from the
        point an even number of times. Without it, each query walks every hole of a plane
        (one per antipad), which is quadratic in the board size. Yields ``(queries, rings)``
        chunks of at most about ``max_vertices`` vertices; ``queries`` are indices into
        ``geom_of`` and the chunk's geometry ``k`` belongs to ``queries[k]``.
        """
        n = len(geom_of)
        tree, ring_geom, nv = self._ring_tree()
        qi, ri = tree.query(shapely.box(box[:, 0], box[:, 1], box[:, 2], box[:, 3]))
        keep = (ring_geom[ri] == geom_of[qi]) & (nv[ri] >= 2)
        qi, ri = qi[keep], ri[keep]
        order = np.lexsort((ri, qi))
        qi, ri = qi[order], ri[order]
        per_q = np.bincount(qi, weights=nv[ri], minlength=n).astype(np.int64)
        q_start = np.concatenate(([0], np.cumsum(np.bincount(qi, minlength=n)))).astype(np.int64)
        lo = 0
        cum = np.cumsum(per_q)
        while lo < n:
            base = cum[lo - 1] if lo else 0
            hi = int(np.searchsorted(cum, base + max_vertices, side="right"))
            hi = min(max(hi, lo + 1), n)
            sel = slice(int(q_start[lo]), int(q_start[hi]))
            rr, rq = ri[sel], qi[sel] - lo
            sub = RingSet.__new__(RingSet)
            lens = nv[rr]
            offs = np.cumsum(lens) - lens
            vidx = np.arange(int(lens.sum()), dtype=np.int64) - np.repeat(offs, lens)
            vidx += np.repeat(self.ring_start[rr], lens)
            sub.xy = np.ascontiguousarray(self.xy[vidx])
            sub.ring_start = np.concatenate(([0], np.cumsum(lens))).astype(np.int64)
            sub.geom_start = np.concatenate(
                ([0], np.cumsum(np.bincount(rq, minlength=hi - lo)))
            ).astype(np.int64)
            sub._tree = None
            yield np.arange(lo, hi, dtype=np.intp), sub
            lo = hi
