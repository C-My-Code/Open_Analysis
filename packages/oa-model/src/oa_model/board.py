# SPDX-License-Identifier: GPL-3.0-or-later
"""Components, outline, metadata and the Board root (board-model.md section 10)."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from oa_model._base import OAModel
from oa_model._canon import canon, sha256_json
from oa_model.common import Angle, Diagnostic, ItemId, Length, Side, SourceRef, Vec2
from oa_model.copper import CopperShape, Track, Zone
from oa_model.geometry import PathSeg, Polygon
from oa_model.migrations import SCHEMA_VERSION
from oa_model.nets import DiffPair, Net, NetClass
from oa_model.pads import Pad
from oa_model.padstack import Padstack
from oa_model.stackup import Stackup
from oa_model.vias import Via


class Component(OAModel):
    refdes: str = Field(
        description="Reference designator, unique; the component's key.", examples=["U1"]
    )
    value: str | None = Field(default=None, description="Component value.")
    footprint: str | None = Field(default=None, description="'Library:Name'.")
    side: Side = Field(description="Board side the component is mounted on.")
    position: Vec2 = Field(description="Board-frame (x, y), metres.")
    rotation: Angle = Field(description="Rotation, radians, counter-clockwise.")
    pads: tuple[ItemId, ...] = Field(description="Ids of the component's Pads.")
    courtyard: Polygon | None = Field(default=None, description="Courtyard polygon, board frame.")
    body_height: Length | None = Field(default=None, description="Body height, metres.")
    dnp: bool = Field(default=False, description="Do not populate.")
    exclude_from_bom: bool = Field(default=False, description="Excluded from the BOM.")
    fields: tuple[tuple[str, str], ...] = Field(
        default=(), description="Selected properties as (name, value) pairs: MPN, Manufacturer."
    )
    model_ref: str | None = Field(default=None, description="3D model path (metadata).")
    source: SourceRef | None = Field(default=None, description="Originating item.")


class BoardOutline(OAModel):
    polygon: Polygon = Field(description="Outer edge (exterior) plus internal cutouts (holes).")
    edges: tuple[PathSeg, ...] = Field(
        default=(), description="Exterior edge as analytic segments (CCW), optional."
    )
    extra_boards: tuple[Polygon, ...] = Field(
        default=(), description="Additional disjoint outlines (panels)."
    )


class BoardMeta(OAModel):
    name: str = Field(description="Board name.")
    source_tool: Literal["kicad", "ipc2581", "altium", "odbpp", "synthetic"] = Field(
        description="Tool the board was read from."
    )
    source_version: str | None = Field(default=None, description="Source format version.")
    source_file: str | None = Field(default=None, description="Source file name (not a path).")
    source_hash: str | None = Field(default=None, description="sha256 of the source file bytes.")
    reader: str = Field(description="Reader name and version. Excluded from content_hash.")
    arc_tolerance: Length = Field(default=1e-6, description="Max arc sagitta error, metres.")
    created_utc: str = Field(description="ISO-8601 creation time. Excluded from content_hash.")


class Board(OAModel):
    """Neutral, tool-independent description of a PCB. SI units; X right, Y up, z=0 at stack bottom."""

    schema_version: Annotated[str, StringConstraints(pattern=r"^1\.\d+$")] = Field(
        default=SCHEMA_VERSION, description="Schema 'MAJOR.MINOR'."
    )
    meta: BoardMeta = Field(description="Provenance metadata.")
    outline: BoardOutline = Field(description="Board outline.")
    stackup: Stackup = Field(description="Physical stackup.")
    nets: tuple[Net, ...] = Field(description="All nets, unique by name.")
    net_classes: tuple[NetClass, ...] = Field(
        default=(), description="Net classes, unique by name."
    )
    diff_pairs: tuple[DiffPair, ...] = Field(default=(), description="Differential pairs.")
    padstacks: tuple[Padstack, ...] = Field(default=(), description="Padstacks, unique by id.")
    pads: tuple[Pad, ...] = Field(default=(), description="All pads.")
    vias: tuple[Via, ...] = Field(default=(), description="All vias.")
    tracks: tuple[Track, ...] = Field(default=(), description="All track segments.")
    zones: tuple[Zone, ...] = Field(default=(), description="All zones.")
    shapes: tuple[CopperShape, ...] = Field(default=(), description="Graphic copper shapes.")
    components: tuple[Component, ...] = Field(default=(), description="Components by refdes.")
    diagnostics: tuple[Diagnostic, ...] = Field(default=(), description="Findings from ingest.")

    def content_hash(self) -> str:
        """'sha256:' + hex over a canonical encoding (board-model.md 14.4).

        Sorted-key JSON, exact float repr, arrays replaced by their digests, and
        `meta.created_utc` / `meta.reader` excluded.
        """
        dump = self.model_dump(mode="python", exclude={"meta": {"created_utc", "reader"}})
        return "sha256:" + sha256_json(canon(dump))
