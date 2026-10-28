# SPDX-License-Identifier: GPL-3.0-or-later
"""CrossSection: input to the 2D impedance solver (interfaces.md section 1).

Coordinates are (u, z): u lateral, z vertical.
"""

from __future__ import annotations

from typing import Annotated, Literal

import numpy as np
from pydantic import Field, model_validator

from oa_model._base import OAModel
from oa_model._canon import canon, sha256_json
from oa_model.arrays import Points
from oa_model.common import ItemId, LayerId, Length, NetId, Vec2
from oa_model.geometry import Polygon
from oa_model.materials import DielectricModel, NoRoughness, Roughness


class XsTrapezoid(OAModel):
    kind: Literal["trapezoid"] = Field(
        default="trapezoid", description="Discriminator tag; always 'trapezoid'."
    )
    u_center: float = Field(description="Lateral centre of the shape, metres.")
    z_bottom: float = Field(description="Bottom z, metres.")
    w_bottom: Length = Field(gt=0, description="Bottom width, metres.")
    w_top: Length = Field(gt=0, description="Top width, metres (== w_bottom for a rectangle).")
    thickness: Length = Field(gt=0, description="Height, metres.")


class XsPolygon(OAModel):
    kind: Literal["polygon"] = Field(
        default="polygon", description="Discriminator tag; always 'polygon'."
    )
    polygon: Polygon = Field(description="(u, z) coordinates in the polygon's x, y slots, metres.")


class XsCircle(OAModel):
    """Analytic validation shapes (coax, wire over plane)."""

    kind: Literal["circle"] = Field(
        default="circle", description="Discriminator tag; always 'circle'."
    )
    u_center: float = Field(description="Centre u, metres.")
    z_center: float = Field(description="Centre z, metres.")
    radius: Length = Field(gt=0, description="Radius, metres.")


class XsPlane(OAModel):
    """Horizontal ground/reference conductor; None bounds mean infinite."""

    kind: Literal["plane"] = Field(
        default="plane", description="Discriminator tag; always 'plane'."
    )
    z_bottom: float = Field(description="Bottom z, metres.")
    thickness: Length = Field(description="Thickness, metres; 0 = zero-thickness plane.")
    u_min: float | None = Field(default=None, description="Left edge u, metres; None = infinite.")
    u_max: float | None = Field(default=None, description="Right edge u, metres; None = infinite.")


XsShape = Annotated[XsTrapezoid | XsPolygon | XsCircle | XsPlane, Field(discriminator="kind")]


class XsConductor(OAModel):
    id: str = Field(description="'s1', 's2', 'g1', ...: unique in the section.")
    role: Literal["signal", "ground", "floating"] = Field(description="Electrical role.")
    shape: XsShape = Field(description="Conductor geometry.")
    net: NetId | None = Field(default=None, description="Provenance only; NOT part of the key.")
    conductivity: float = Field(default=5.8e7, gt=0, description="Conductivity, S/m.")
    roughness_top: Roughness = Field(default=NoRoughness(), description="Top surface roughness.")
    roughness_bottom: Roughness = Field(
        default=NoRoughness(), description="Bottom surface roughness."
    )
    roughness_side: Roughness = Field(default=NoRoughness(), description="Sidewall roughness.")


class XsSlab(OAModel):
    kind: Literal["slab"] = Field(default="slab", description="Discriminator tag; always 'slab'.")
    z_bottom: float = Field(description="Bottom z, metres.")
    z_top: float = Field(description="Top z, metres.")
    u_min: float | None = Field(default=None, description="Left edge, metres; None = infinite.")
    u_max: float | None = Field(default=None, description="Right edge, metres; None = infinite.")


class XsRegion(OAModel):
    kind: Literal["region"] = Field(
        default="region", description="Discriminator tag; always 'region'."
    )
    polygon: Polygon = Field(description="(u, z) polygon, e.g. conformal solder mask.")


class XsDielectric(OAModel):
    region: Annotated[XsSlab | XsRegion, Field(discriminator="kind")] = Field(
        description="Where this dielectric is."
    )
    model: DielectricModel = Field(description="Frequency-dependent Dk/Df model.")
    dk_z_over_dk_xy: float = Field(default=1.0, gt=0, description="Anisotropy ratio.")
    priority: int = Field(
        default=0, description="Higher wins where regions overlap; slabs 0, regions 10."
    )


class XsProvenance(OAModel):
    board_hash: str | None = Field(default=None, description="content_hash of the source board.")
    net_ids: tuple[NetId, ...] = Field(default=(), description="Nets in the section.")
    layer: LayerId | None = Field(default=None, description="Signal layer id.")
    position: Vec2 | None = Field(default=None, description="Board-frame sample point, metres.")
    track_ids: tuple[ItemId, ...] = Field(default=(), description="Source track ids.")


_TopologyHint = Literal[
    "microstrip",
    "embedded_microstrip",
    "stripline",
    "offset_stripline",
    "cpw",
    "cpwg",
    "edge_coupled_microstrip",
    "edge_coupled_stripline",
    "broadside_coupled",
    "coax",
    "wire_over_plane",
    "general",
]

_SIG_KEYS = frozenset(
    {"model", "conductivity", "dk_z_over_dk_xy", "background_dk", "surface_ratio", "flat_ratio"}
)


def _shift_points(p: Points, du: float, dz: float) -> Points:
    return Points(p.array - np.array([du, dz]))


def _shift_polygon(p: Polygon, du: float, dz: float) -> Polygon:
    return Polygon(
        exterior=_shift_points(p.exterior, du, dz),
        holes=tuple(_shift_points(h, du, dz) for h in p.holes),
    )


def _opt(v: float | None, d: float) -> float | None:
    return None if v is None else v - d


def _shift_shape(s: XsShape, du: float, dz: float) -> XsShape:
    if isinstance(s, XsTrapezoid):
        return s.model_copy(update={"u_center": s.u_center - du, "z_bottom": s.z_bottom - dz})
    if isinstance(s, XsPolygon):
        return XsPolygon(polygon=_shift_polygon(s.polygon, du, dz))
    if isinstance(s, XsCircle):
        return s.model_copy(update={"u_center": s.u_center - du, "z_center": s.z_center - dz})
    return s.model_copy(
        update={"z_bottom": s.z_bottom - dz, "u_min": _opt(s.u_min, du), "u_max": _opt(s.u_max, du)}
    )


def _shape_u_range(s: XsShape) -> tuple[float, float] | None:
    if isinstance(s, XsTrapezoid):
        half = max(s.w_bottom, s.w_top) / 2
        return s.u_center - half, s.u_center + half
    if isinstance(s, XsPolygon):
        u = s.polygon.exterior.array[:, 0]
        return float(np.min(u)), float(np.max(u))
    if isinstance(s, XsCircle):
        return s.u_center - s.radius, s.u_center + s.radius
    if s.u_min is None or s.u_max is None:
        return None
    return s.u_min, s.u_max


def _shape_z_min(s: XsShape) -> float:
    if isinstance(s, XsPolygon):
        return float(np.min(s.polygon.exterior.array[:, 1]))
    if isinstance(s, XsCircle):
        return s.z_center - s.radius
    return s.z_bottom


def _mirror_sig(s: XsShape, mirror: bool) -> tuple[object, ...]:
    """Rounded (nm) fingerprint of a shape, mirrored about u=0 when `mirror`."""
    sgn = -1.0 if mirror else 1.0

    def n(x: float) -> int:
        return int(np.floor(x * 1e9 + 0.5))

    if isinstance(s, XsTrapezoid):
        return ("t", n(sgn * s.u_center), n(s.z_bottom), n(s.w_bottom), n(s.w_top), n(s.thickness))
    if isinstance(s, XsCircle):
        return ("c", n(sgn * s.u_center), n(s.z_center), n(s.radius))
    if isinstance(s, XsPlane):
        lo = None if s.u_min is None else n(s.u_min)
        hi = None if s.u_max is None else n(s.u_max)
        if mirror:
            lo, hi = (None if hi is None else -hi), (None if lo is None else -lo)
        return ("p", n(s.z_bottom), n(s.thickness), lo, hi)
    pts = s.polygon.exterior.array * np.array([sgn, 1.0])
    return ("g", tuple(sorted((n(float(x)), n(float(y))) for x, y in pts)))


class CrossSection(OAModel):
    """2D transmission-line cross-section. Canonical form: u=0 at the midpoint of the signal
    conductors' bounding box and z=0 at the lowest material boundary."""

    schema: Literal["oa-xsection/1"] = Field(  # type: ignore[assignment]
        default="oa-xsection/1", description="Schema tag; always 'oa-xsection/1'."
    )
    conductors: tuple[XsConductor, ...] = Field(
        description="Conductors; the ORDER of signal conductors defines matrix index order."
    )
    dielectrics: tuple[XsDielectric, ...] = Field(description="Dielectric regions.")
    background_dk: float = Field(
        default=1.0, ge=1.0, description="Relative permittivity outside every dielectric (air)."
    )
    topology_hint: _TopologyHint = Field(
        default="general", description="Solver hint for fast analytic paths."
    )
    symmetric_about_u0: bool = Field(
        default=False, description="Geometry is mirror symmetric about u=0 (checked)."
    )
    provenance: XsProvenance | None = Field(
        default=None, description="Where the section came from; NOT part of the key."
    )

    @model_validator(mode="after")
    def _check(self) -> CrossSection:
        ids = [c.id for c in self.conductors]
        if len(set(ids)) != len(ids):
            raise ValueError("conductor ids must be unique within the section")
        if not any(c.role == "signal" for c in self.conductors):
            raise ValueError("a cross-section needs at least one signal conductor")
        return self

    def signal_ids(self) -> tuple[str, ...]:
        """Ids of the signal conductors in matrix index order."""
        return tuple(c.id for c in self.conductors if c.role == "signal")

    @classmethod
    def canonicalize(cls, raw: CrossSection) -> CrossSection:
        """Translate so u=0 is the midpoint of the signal conductors' bounding box and z=0 is
        the lowest material boundary. Raises ValueError when `symmetric_about_u0` is set but the
        conductors are not mirror symmetric about the new u=0."""
        ranges = [
            rng
            for c in raw.conductors
            if c.role == "signal" and (rng := _shape_u_range(c.shape)) is not None
        ]
        du = 0.0
        if ranges:
            du = (min(r[0] for r in ranges) + max(r[1] for r in ranges)) / 2
        zs = [_shape_z_min(c.shape) for c in raw.conductors]
        for d in raw.dielectrics:
            if isinstance(d.region, XsSlab):
                zs.append(d.region.z_bottom)
            else:
                zs.append(float(np.min(d.region.polygon.exterior.array[:, 1])))
        dz = min(zs)
        # Shifts are snapped to the 1 nm grid so that sub-nanometre jitter of one coordinate
        # cannot move the whole section across a rounding boundary in key().
        du = float(np.floor(du * 1e9 + 0.5)) * 1e-9
        dz = float(np.floor(dz * 1e9 + 0.5)) * 1e-9
        conductors = tuple(
            c.model_copy(update={"shape": _shift_shape(c.shape, du, dz)}) for c in raw.conductors
        )
        dielectrics = []
        for d in raw.dielectrics:
            r = d.region
            if isinstance(r, XsSlab):
                new_r: XsSlab | XsRegion = r.model_copy(
                    update={
                        "z_bottom": r.z_bottom - dz,
                        "z_top": r.z_top - dz,
                        "u_min": _opt(r.u_min, du),
                        "u_max": _opt(r.u_max, du),
                    }
                )
            else:
                new_r = XsRegion(polygon=_shift_polygon(r.polygon, du, dz))
            dielectrics.append(d.model_copy(update={"region": new_r}))
        out = raw.model_copy(update={"conductors": conductors, "dielectrics": tuple(dielectrics)})
        if raw.symmetric_about_u0:
            fwd = sorted(map(repr, (_mirror_sig(c.shape, False) for c in conductors)))
            mir = sorted(map(repr, (_mirror_sig(c.shape, True) for c in conductors)))
            if fwd != mir:
                raise ValueError("symmetric_about_u0 is set but the conductors are not symmetric")
        return out

    def key(self) -> str:
        """'xs1:' + sha256[:32] of the canonicalized section. Lengths are rounded to 1 nm,
        material parameters to 6 significant digits; provenance and nets are dropped."""
        c = CrossSection.canonicalize(self)
        dump = c.model_dump(
            mode="python", exclude={"provenance": True, "conductors": {"__all__": {"net"}}}
        )
        return "xs1:" + sha256_json(canon(dump, nm=True, sig_keys=_SIG_KEYS))[:32]
