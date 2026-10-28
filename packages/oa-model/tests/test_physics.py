# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import math

import pytest
import yaml

from boards import UM, make_board
from oa_model import (
    ConductorLayer,
    ConstantDkDf,
    DielectricMaterial,
    DjordjevicSarkar,
    PhysicsOverlay,
    SetupError,
    apply_physics,
    validate_board,
)
from oa_model.materials import CannonballHuray, library_entries, library_material
from oa_model.physics import LayerOverride, LayerSelector
from oa_model.stackup import DielectricLayer, MaskLayer

# The example sidecar from board-model.md section 11.
SIDECAR = """
schema: oa-physics/1
materials:
  - kind: dielectric
    name: Isola_370HR_1080
    model: {kind: djordjevic_sarkar, dk: 3.92, df: 0.021, f_ref: 1GHz}
    dk_z_over_dk_xy: 0.95
    thermal: {k_xy: 0.8, k_z: 0.4}
  - kind: conductor
    name: copper_ed
    conductivity: 5.8e7
aliases:
  - {source_name: "FR4", material: Isola_370HR_1080}
layers:
  - select: {kinds: [conductor]}
    roughness_top: {kind: cannonball_huray, rz: 2.5um}
    roughness_bottom: {kind: cannonball_huray, rz: 5.0um}
    etch: {kind: angle, value: 60deg}
  - select: {source_names: ["F.Cu", "B.Cu"]}
    thickness: 48um
via_plating_thickness: 25um
"""


def test_spec_example_sidecar() -> None:
    b = make_board()
    overlay = PhysicsOverlay.model_validate(yaml.safe_load(SIDECAR))
    out = apply_physics(b, overlay)

    by_id = {layer.id: layer for layer in out.stackup.layers}
    # alias FR4 -> Isola on every dielectric layer
    for did in ("D1", "D2", "D3"):
        layer = by_id[did]
        assert isinstance(layer, DielectricLayer)
        assert layer.material == "Isola_370HR_1080"
    # roughness and etch on all conductors
    for cid in ("L1", "L2", "L3", "L4"):
        layer = by_id[cid]
        assert isinstance(layer, ConductorLayer)
        assert isinstance(layer.roughness_top, CannonballHuray)
        assert isinstance(layer.roughness_bottom, CannonballHuray)
        assert layer.roughness_top.rz == pytest.approx(2.5e-6)
        assert layer.roughness_bottom.rz == pytest.approx(5.0e-6)
        assert layer.etch is not None
        assert layer.etch.kind == "angle"
        assert layer.etch.value == pytest.approx(math.radians(60))
    # outer copper 48 um, inner untouched, z recomputed
    assert by_id["L1"].thickness == pytest.approx(48 * UM)
    assert by_id["L4"].thickness == pytest.approx(48 * UM)
    assert by_id["L2"].thickness == pytest.approx(35 * UM)
    assert out.stackup.total_thickness == pytest.approx(b.stackup.total_thickness + 26 * UM)
    assert by_id["MB"].z_bottom == 0.0
    assert out.stackup.layers[0].z_top == pytest.approx(out.stackup.total_thickness)
    # via plating
    assert out.padstacks[1].drill is not None
    assert out.padstacks[1].drill.plating_thickness == pytest.approx(25 * UM)
    # materials: only the referenced ones; sidecar source
    mats = {m.name: m for m in out.stackup.materials}
    assert set(mats) == {"copper", "Isola_370HR_1080", "mask"}
    isola = mats["Isola_370HR_1080"]
    assert isinstance(isola, DielectricMaterial)
    assert isola.source == "sidecar"
    assert isola.dk_z_over_dk_xy == 0.95
    # the board stays valid and the original is untouched
    assert validate_board(out, strict=True) == ()
    assert isinstance(b.stackup.layers[2], DielectricLayer)
    assert b.stackup.layers[2].material == "FR4"
    # deterministic and hash-visible
    assert apply_physics(b, overlay).content_hash() == out.content_hash()
    assert out.content_hash() != b.content_hash()


def test_constant_dkdf_upgraded_to_ds() -> None:
    b = make_board()
    out = apply_physics(b, PhysicsOverlay())
    mats = {m.name: m for m in out.stackup.materials}
    fr4 = mats["FR4"]
    assert isinstance(fr4, DielectricMaterial)
    assert isinstance(fr4.model, DjordjevicSarkar)
    assert (fr4.model.dk, fr4.model.df, fr4.model.f_ref) == (4.3, 0.02, 1e9)
    assert isinstance(mats["mask"], DielectricMaterial)
    assert isinstance(mats["mask"].model, DjordjevicSarkar)
    # opt-out keeps the constant model
    keep = PhysicsOverlay.model_validate(
        {"schema": "oa-physics/1", "defaults": {"dielectric_model_upgrade": "none"}}
    )
    kept = {m.name: m for m in apply_physics(b, keep).stackup.materials}["FR4"]
    assert isinstance(kept, DielectricMaterial)
    assert isinstance(kept.model, ConstantDkDf)


def test_library_material_by_override() -> None:
    b = make_board()
    ov = PhysicsOverlay(
        layers=(LayerOverride(select=LayerSelector(ids=("D2",)), material="Rogers_RO4350B"),)
    )
    out = apply_physics(b, ov)
    d2 = next(layer for layer in out.stackup.layers if layer.id == "D2")
    assert d2.material == "Rogers_RO4350B"
    rog = next(m for m in out.stackup.materials if m.name == "Rogers_RO4350B")
    assert rog.source == "library"


def test_errors_are_structured() -> None:
    b = make_board()
    bad_mat = PhysicsOverlay(
        layers=(LayerOverride(select=LayerSelector(ids=("D1",)), material="nope"),)
    )
    with pytest.raises(SetupError) as ei:
        apply_physics(b, bad_mat)
    d = ei.value.diagnostics[0]
    assert d.code == "PHYSICS.UNKNOWN_MATERIAL"
    assert d.path == "/layers/0/material"
    assert d.hint

    wrong_kind = PhysicsOverlay(
        layers=(LayerOverride(select=LayerSelector(ids=("D1",)), material="copper_ed"),)
    )
    with pytest.raises(SetupError, match="conductor"):
        apply_physics(b, wrong_kind)

    on_dielectric = PhysicsOverlay.model_validate(
        {
            "schema": "oa-physics/1",
            "layers": [
                {"select": {"kinds": ["dielectric"]}, "etch": {"kind": "angle", "value": 1.0}}
            ],
        }
    )
    with pytest.raises(SetupError, match="conductor layers only"):
        apply_physics(b, on_dielectric)

    with pytest.raises(ValueError):
        LayerSelector()


def test_mask_layer_untouched_kind() -> None:
    out = apply_physics(make_board(), PhysicsOverlay())
    assert isinstance(out.stackup.layers[0], MaskLayer)


def test_material_library() -> None:
    entries = library_entries()
    names = {e.material.name for e in entries}
    assert len(entries) >= 8
    assert {
        "FR4_generic",
        "Isola_370HR_1080",
        "Megtron6_generic",
        "Rogers_RO4350B",
        "PTFE_generic",
        "LPI_soldermask_generic",
        "copper_ed",
        "copper_rolled",
    } <= names
    assert all(len(e.citation) > 40 for e in entries)
    assert library_material("nope") is None
