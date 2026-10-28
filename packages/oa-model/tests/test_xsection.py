# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from oa_model import ConstantDkDf, CrossSection, DjordjevicSarkar, Points, Polygon
from oa_model.materials import Huray
from oa_model.xsection import (
    XsConductor,
    XsDielectric,
    XsPlane,
    XsPolygon,
    XsProvenance,
    XsRegion,
    XsSlab,
    XsTrapezoid,
)

UM = 1e-6
NM = 1e-9


def microstrip(
    du: float = 0.0,
    dz: float = 0.0,
    *,
    w: float = 100 * UM,
    h: float = 200 * UM,
    net: str | None = "SIG",
    prov: XsProvenance | None = None,
    dk: float = 4.3,
    ground_z: float = 0.0,
    mask: bool = False,
) -> CrossSection:
    diel = [
        XsDielectric(
            region=XsSlab(z_bottom=ground_z + dz, z_top=ground_z + h + dz),
            model=DjordjevicSarkar(dk=dk, df=0.02),
        )
    ]
    if mask:
        poly = Polygon(
            exterior=Points(
                np.array([[-1e-3, h], [1e-3, h], [1e-3, h + 20 * UM], [-1e-3, h + 20 * UM]])
                + np.array([du, dz])
            )
        )
        diel.append(
            XsDielectric(
                region=XsRegion(polygon=poly), model=ConstantDkDf(dk=3.5, df=0.02), priority=10
            )
        )
    return CrossSection(
        conductors=(
            XsConductor(
                id="s1",
                role="signal",
                net=net,
                shape=XsTrapezoid(
                    u_center=300 * UM + du,
                    z_bottom=h + dz,
                    w_bottom=w,
                    w_top=w - 20 * UM,
                    thickness=35 * UM,
                ),
                roughness_top=Huray(sphere_radius=0.5 * UM, surface_ratio=2.9),
            ),
            XsConductor(
                id="g1",
                role="ground",
                shape=XsPlane(z_bottom=ground_z - 35 * UM + dz, thickness=35 * UM),
            ),
        ),
        dielectrics=tuple(diel),
        topology_hint="microstrip",
        provenance=prov,
    )


def test_canonicalize_origin() -> None:
    cs = CrossSection.canonicalize(microstrip(du=0.0123, dz=0.00456))
    sig = cs.conductors[0].shape
    assert isinstance(sig, XsTrapezoid)
    assert abs(sig.u_center) < 1e-9  # signal bbox midpoint at u = 0 (to the 1 nm grid)
    zs = [c.shape.z_bottom for c in cs.conductors if isinstance(c.shape, XsPlane)]
    assert min(zs) == pytest.approx(0.0, abs=1e-9)  # lowest boundary at z = 0
    assert cs.signal_ids() == ("s1",)


def test_key_invariant_under_translation() -> None:
    base = microstrip().key()
    assert base.startswith("xs1:")
    assert len(base) == 4 + 32
    for du, dz in [(0.0, 0.0), (1e-3, 0.0), (-0.25, 0.5), (3 * UM, -7 * UM), (12.345, 0.001)]:
        assert microstrip(du, dz).key() == base


def test_key_invariant_under_provenance_and_net() -> None:
    base = microstrip().key()
    prov = XsProvenance(board_hash="sha256:abc", net_ids=("X",), layer="L1", position=(1.0, 2.0))
    assert microstrip(net="OTHER", prov=prov).key() == base
    assert microstrip(net=None).key() == base


def test_key_changes_under_2nm_move() -> None:
    base = microstrip().key()
    assert microstrip(w=100 * UM + 2 * NM).key() != base
    assert microstrip(h=200 * UM + 2 * NM).key() != base
    assert microstrip(ground_z=2 * NM).key() != base


def test_key_stable_under_sub_nm_jitter() -> None:
    base = microstrip().key()
    for jitter in (0.4 * NM, -0.4 * NM, 0.1 * NM):
        assert microstrip(w=100 * UM + jitter).key() == base
        assert microstrip(h=200 * UM + jitter).key() == base
        assert microstrip(ground_z=jitter).key() == base
    assert microstrip(dk=4.3000001).key() == microstrip(dk=4.3).key()  # 6 significant digits
    assert microstrip(dk=4.31).key() != microstrip(dk=4.3).key()


def test_key_covers_regions_and_order() -> None:
    assert microstrip(mask=True).key() != microstrip().key()
    assert microstrip(5e-3, 1e-3, mask=True).key() == microstrip(mask=True).key()
    cs = microstrip()
    swapped = cs.model_copy(update={"conductors": tuple(reversed(cs.conductors))})
    # order is part of the key (matrix index order)
    assert swapped.key() != cs.key()


def test_validation() -> None:
    with pytest.raises(ValidationError, match="unique"):
        cs = microstrip()
        CrossSection(conductors=(cs.conductors[0], cs.conductors[0]), dielectrics=())
    with pytest.raises(ValidationError, match="signal"):
        CrossSection(conductors=(microstrip().conductors[1],), dielectrics=())


def test_symmetry_check() -> None:
    def pair(offset: float, flagged: bool = True) -> CrossSection:
        def trap(i: str, u: float, w: float = 100 * UM) -> XsConductor:
            return XsConductor(
                id=i,
                role="signal",
                shape=XsTrapezoid(
                    u_center=u,
                    z_bottom=200 * UM,
                    w_bottom=w,
                    w_top=w,
                    thickness=35 * UM,
                ),
            )

        return CrossSection(
            conductors=(trap("s1", -150 * UM), trap("s2", 150 * UM, 100 * UM + offset)),
            dielectrics=(
                XsDielectric(
                    region=XsSlab(z_bottom=0.0, z_top=200 * UM), model=ConstantDkDf(dk=4.0, df=0.01)
                ),
            ),
            symmetric_about_u0=flagged,
        )

    CrossSection.canonicalize(pair(0.0))
    with pytest.raises(ValueError, match="symmetric"):
        CrossSection.canonicalize(pair(20 * UM))
    CrossSection.canonicalize(pair(20 * UM, flagged=False))


def test_polygon_shape_roundtrip() -> None:
    sq = Polygon(exterior=Points([0, 0, 1e-4, 0, 1e-4, 1e-4, 0, 1e-4]))
    cs = CrossSection(
        conductors=(XsConductor(id="s1", role="signal", shape=XsPolygon(polygon=sq)),),
        dielectrics=(),
    )
    again = CrossSection.model_validate_json(cs.model_dump_json())
    assert again == cs
    assert again.key() == cs.key()
