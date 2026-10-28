# SPDX-License-Identifier: GPL-3.0-or-later
"""Physics sidecar (`<project>.oa-physics.yaml`) and `apply_physics` (board-model.md section 11)."""

from __future__ import annotations

import fnmatch
import logging
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from oa_model._base import OAModel
from oa_model.board import Board
from oa_model.common import Diagnostic, Length, Temperature
from oa_model.errors import SetupError
from oa_model.materials import (
    ConstantDkDf,
    DielectricMaterial,
    DjordjevicSarkar,
    Material,
    NoRoughness,
    Roughness,
    library_material,
)
from oa_model.stackup import (
    ConductorLayer,
    DielectricLayer,
    Etch,
    StackLayer,
    Stackup,
)

_log = logging.getLogger("oa.model.physics")


class LayerSelector(OAModel):
    """Selects stack layers by id ('L3'), source name ('In2.Cu'), kind or glob. A layer matches
    when ANY criterion matches; at least one criterion is required."""

    ids: tuple[str, ...] = Field(default=(), description="Layer ids or globs: 'L3', 'L*'.")
    source_names: tuple[str, ...] = Field(
        default=(), description="Source layer names or globs: 'In2.Cu', '*.Cu'."
    )
    kinds: tuple[Literal["conductor", "dielectric", "mask"], ...] = Field(
        default=(), description="Layer kinds."
    )

    @model_validator(mode="after")
    def _non_empty(self) -> LayerSelector:
        if not (self.ids or self.source_names or self.kinds):
            raise ValueError("a LayerSelector needs at least one of ids, source_names, kinds")
        return self

    def matches(self, layer: StackLayer) -> bool:
        if layer.kind in self.kinds:
            return True
        if any(fnmatch.fnmatchcase(layer.id, p) for p in self.ids):
            return True
        name = layer.source_name
        return name is not None and any(fnmatch.fnmatchcase(name, p) for p in self.source_names)


class LayerOverride(OAModel):
    select: LayerSelector = Field(description="Which layers this override applies to.")
    material: str | None = Field(default=None, description="Replace the material reference.")
    thickness: Length | None = Field(
        default=None, description="Thickness in metres; z is recomputed for the whole stack."
    )
    roughness_top: Roughness | None = Field(default=None, description="Conductor layers only.")
    roughness_bottom: Roughness | None = Field(default=None, description="Conductor layers only.")
    roughness_side: Roughness | None = Field(default=None, description="Conductor layers only.")
    etch: Etch | None = Field(default=None, description="Conductor layers only.")
    plating_thickness: Length | None = Field(default=None, description="Conductor layers only.")
    construction: Literal["core", "prepreg"] | None = Field(
        default=None, description="Dielectric layers only."
    )


class MaterialAlias(OAModel):
    """Maps a source material name ('FR4', '2116 RC58%') to a library or sidecar material."""

    source_name: str = Field(description="Material name as found in the board.")
    material: str = Field(description="Name of the replacement material.")


class PhysicsDefaults(OAModel):
    conductor_material: str = Field(
        default="copper",
        description="Material for conductor layers whose material came from ingest/defaults.",
    )
    roughness: Roughness = Field(
        default=NoRoughness(), description="Roughness for conductor surfaces that have none."
    )
    dielectric_model_upgrade: Literal["none", "djordjevic_sarkar"] = Field(
        default="djordjevic_sarkar",
        description="'djordjevic_sarkar': every ConstantDkDf left after aliasing becomes DS.",
    )


class PhysicsOverlay(OAModel):
    schema: Literal["oa-physics/1"] = Field(  # type: ignore[assignment]
        default="oa-physics/1", description="Schema tag; always 'oa-physics/1'."
    )
    materials: tuple[Material, ...] = Field(
        default=(), description="New or replacing materials (by name)."
    )
    aliases: tuple[MaterialAlias, ...] = Field(default=(), description="Material renames.")
    layers: tuple[LayerOverride, ...] = Field(default=(), description="Applied in order.")
    defaults: PhysicsDefaults = Field(default=PhysicsDefaults(), description="Global defaults.")
    via_plating_thickness: Length | None = Field(
        default=None, description="Barrel plating of plated drills, metres."
    )
    temperature: Temperature = Field(
        default=298.15, description="Board temperature, K, for rho(T) unless overridden."
    )


def load_physics(path: str | Path) -> PhysicsOverlay:
    """Load and validate a physics sidecar YAML file."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return PhysicsOverlay.model_validate(data)


def _fail(code: str, message: str, hint: str, path: str | None = None) -> SetupError:
    diag = Diagnostic(severity="error", code=code, message=message, hint=hint, path=path)
    return SetupError(message, (diag,))


def apply_physics(board: Board, overlay: PhysicsOverlay) -> Board:
    """Return a new Board with the overlay merged. Pure and deterministic.

    Order: sidecar materials, aliases, defaults, layer overrides (in order), z recompute,
    Dk/Df upgrade, via plating. Materials are then resolved against sidecar, stackup, and the
    shipped library; the final stackup lists only the materials the layers reference.
    """
    stack = board.stackup
    pool: dict[str, Material] = {m.name: m for m in stack.materials}
    for m in overlay.materials:
        pool[m.name] = m.model_copy(update={"source": "sidecar"})
    alias = {a.source_name: a.material for a in overlay.aliases}

    def resolve(name: str, where: str) -> Material:
        found = pool.get(name) or library_material(name)
        if found is None:
            raise _fail(
                "PHYSICS.UNKNOWN_MATERIAL",
                f"material {name!r} is not defined by the sidecar, the board or the library",
                "define it under 'materials:' in the physics sidecar or fix the name",
                where,
            )
        return found

    layers: list[StackLayer] = []
    for layer in stack.layers:
        updates: dict[str, object] = {}
        if layer.material in alias:
            updates["material"] = alias[layer.material]
        if isinstance(layer, ConductorLayer):
            current = pool.get(str(updates.get("material", layer.material)))
            if (
                current is not None
                and current.source in ("ingest", "default")
                and overlay.defaults.conductor_material != layer.material
            ):
                updates["material"] = overlay.defaults.conductor_material
            if not isinstance(overlay.defaults.roughness, NoRoughness):
                for f in ("roughness_top", "roughness_bottom", "roughness_side"):
                    if isinstance(getattr(layer, f), NoRoughness):
                        updates[f] = overlay.defaults.roughness
        layers.append(layer.model_copy(update=updates) if updates else layer)

    restack = False
    for oi, ov in enumerate(overlay.layers):
        hit = False
        for i, layer in enumerate(layers):
            if not ov.select.matches(layer):
                continue
            hit = True
            updates = {}
            where = f"/layers/{oi}"
            if ov.material is not None:
                mat = resolve(ov.material, f"{where}/material")
                want = "conductor" if isinstance(layer, ConductorLayer) else "dielectric"
                if mat.kind != want:
                    raise _fail(
                        "PHYSICS.MATERIAL_KIND",
                        f"material {ov.material!r} is a {mat.kind} but layer {layer.id} needs a "
                        f"{want} material",
                        "pick a material of the right kind",
                        f"{where}/material",
                    )
                updates["material"] = ov.material
            if ov.thickness is not None:
                updates["thickness"] = ov.thickness
                restack = True
            conductor_only = {
                "roughness_top": ov.roughness_top,
                "roughness_bottom": ov.roughness_bottom,
                "roughness_side": ov.roughness_side,
                "etch": ov.etch,
                "plating_thickness": ov.plating_thickness,
            }
            for f, v in conductor_only.items():
                if v is None:
                    continue
                if not isinstance(layer, ConductorLayer):
                    raise _fail(
                        "PHYSICS.LAYER_KIND",
                        f"override field {f!r} applies to conductor layers only (matched {layer.id})",
                        "narrow the selector with kinds: [conductor]",
                        f"{where}/{f}",
                    )
                updates[f] = v
            if ov.construction is not None:
                if not isinstance(layer, DielectricLayer):
                    raise _fail(
                        "PHYSICS.LAYER_KIND",
                        f"'construction' applies to dielectric layers only (matched {layer.id})",
                        "narrow the selector with kinds: [dielectric]",
                        f"{where}/construction",
                    )
                updates["construction"] = ov.construction
            if updates:
                layers[i] = layer.model_copy(update=updates)
        if not hit:
            _log.warning("physics override %d matched no layer", oi)

    total = stack.total_thickness
    if restack:
        z = 0.0
        for i in range(len(layers) - 1, -1, -1):
            layer = layers[i]
            layers[i] = layer.model_copy(update={"z_bottom": z, "z_top": z + layer.thickness})
            z += layer.thickness
        total = z

    used: list[Material] = []
    seen: set[str] = set()
    for layer in layers:
        if layer.material in seen:
            continue
        seen.add(layer.material)
        mat = resolve(layer.material, f"/stackup/layers/{layer.id}/material")
        if (
            overlay.defaults.dielectric_model_upgrade == "djordjevic_sarkar"
            and isinstance(mat, DielectricMaterial)
            and isinstance(mat.model, ConstantDkDf)
        ):
            ds = DjordjevicSarkar(dk=mat.model.dk, df=mat.model.df, f_ref=mat.model.f_ref)
            mat = mat.model_copy(update={"model": ds})
        used.append(mat)

    padstacks = board.padstacks
    if overlay.via_plating_thickness is not None:
        padstacks = tuple(
            p.model_copy(
                update={
                    "drill": p.drill.model_copy(
                        update={"plating_thickness": overlay.via_plating_thickness}
                    )
                }
            )
            if p.drill is not None and p.drill.kind == "plated"
            else p
            for p in board.padstacks
        )

    new_stack: Stackup = stack.model_copy(
        update={"layers": tuple(layers), "materials": tuple(used), "total_thickness": total}
    )
    return board.model_copy(update={"stackup": new_stack, "padstacks": padstacks})
