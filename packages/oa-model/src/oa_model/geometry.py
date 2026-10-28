# SPDX-License-Identifier: GPL-3.0-or-later
"""Geometry primitives (board-model.md section 5) and ring helpers."""

from typing import Annotated, Literal

import numpy as np
import numpy.typing as npt
from pydantic import Field, model_validator

from oa_model._base import GEOM_EPS, OAModel
from oa_model.arrays import Points
from oa_model.common import Vec2


def signed_area(ring: Points | npt.NDArray[np.float64]) -> float:
    """Shoelace signed area of a ring: positive for counter-clockwise."""
    a = ring.array if isinstance(ring, Points) else ring
    if a.shape[0] < 3:
        return 0.0
    x, y = a[:, 0], a[:, 1]
    return float(0.5 * np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def orient_ring(ring: Points, ccw: bool) -> Points:
    """Return `ring` oriented counter-clockwise (ccw=True) or clockwise (ccw=False)."""
    area = signed_area(ring)
    if (area > 0) == ccw or area == 0.0:
        return ring
    return Points(ring.array[::-1])


class Polygon(OAModel):
    """Simple polygon with holes. Discretized (no analytic arcs). Exterior CCW, holes CW."""

    exterior: Points = Field(
        description="Exterior ring, counter-clockwise, not closed (first point not repeated)."
    )
    holes: tuple[Points, ...] = Field(
        default=(), description="Hole rings, clockwise, strictly inside the exterior."
    )

    def normalized(self) -> "Polygon":
        """Return a copy with exterior CCW and holes CW."""
        return Polygon(
            exterior=orient_ring(self.exterior, True),
            holes=tuple(orient_ring(h, False) for h in self.holes),
        )


class LineSeg(OAModel):
    kind: Literal["line"] = Field(default="line", description="Discriminator tag; always 'line'.")
    start: Vec2 = Field(description="Start point (x, y) in metres.")
    end: Vec2 = Field(description="End point (x, y) in metres.")


class ArcSeg(OAModel):
    """Circular arc. Readers convert KiCad's 3-point arcs to this form."""

    kind: Literal["arc"] = Field(default="arc", description="Discriminator tag; always 'arc'.")
    start: Vec2 = Field(description="Start point (x, y) in metres.")
    end: Vec2 = Field(description="End point (x, y) in metres.")
    center: Vec2 = Field(description="Arc centre (x, y) in metres.")
    ccw: bool = Field(description="True when travel from start to end is counter-clockwise.")

    @model_validator(mode="after")
    def _radius_consistent(self) -> "ArcSeg":
        r0 = np.hypot(self.start[0] - self.center[0], self.start[1] - self.center[1])
        r1 = np.hypot(self.end[0] - self.center[0], self.end[1] - self.center[1])
        if abs(r0 - r1) > GEOM_EPS:
            raise ValueError("arc start and end are not equidistant from the centre")
        return self


PathSeg = Annotated[LineSeg | ArcSeg, Field(discriminator="kind")]
