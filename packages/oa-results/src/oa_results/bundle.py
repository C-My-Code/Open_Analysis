# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Result bundle and item containers (interfaces.md section 4.1)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator

from oa_model._base import OAModel
from oa_model.common import Diagnostic
from oa_results.artifact import ArtifactRef
from oa_results.container import PayloadBase, ResultContainer
from oa_results.extraction import ExtractionResult, FarFieldResult
from oa_results.fields import FieldResult
from oa_results.network import NetworkResult
from oa_results.profile import ProfileResult
from oa_results.rlgc import RLGCResultSet
from oa_results.scalars import ScalarSet
from oa_results.tables import TableResult
from oa_results.violations import ViolationSet

ItemKind = Literal[
    "network",
    "field",
    "table",
    "violations",
    "profile",
    "scalars",
    "rlgc",
    "farfield",
    "extraction",
    "artifact",
]


class ArtifactOnly(PayloadBase):
    """An item that is just a file (mesh preview, export, log)."""

    kind: Literal["artifact"] = "artifact"
    artifact: ArtifactRef

    def _digest(self) -> str:
        a = self.artifact
        return f"Artifact {a.name} ({a.media_type}, {a.size} bytes)."

    def _stats(self, max_items: int) -> dict[str, JsonValue]:
        return self.artifact.model_dump(mode="json")


ResultData = Annotated[
    NetworkResult
    | FieldResult
    | TableResult
    | ViolationSet
    | ProfileResult
    | ScalarSet
    | RLGCResultSet
    | FarFieldResult
    | ExtractionResult
    | ArtifactOnly,
    Field(discriminator="kind"),
]


class ResultItem(OAModel):
    id: str  # unique within the bundle
    title: str
    kind: ItemKind
    data: ResultData  # discriminated by kind

    @model_validator(mode="after")
    def _kind_matches(self) -> ResultItem:
        if self.data.kind != self.kind:
            raise ValueError(f"item kind {self.kind!r} does not match data kind {self.data.kind!r}")
        return self


class Provenance(OAModel):
    analysis: str
    analysis_version: str
    board_hash: str
    physics_hash: str | None
    setup_hash: str
    config: dict[str, JsonValue]
    solver: str | None = None
    solver_version: str | None = None
    host: str | None = None
    started_utc: str
    finished_utc: str
    inputs_hash: str | None = (
        None  # sha256 over board/physics/setup/config hashes (AI-facing addition)
    )


class ResultBundle(ResultContainer):
    # ``schema`` shadows a BaseModel attribute, so the field is ``schema_`` with alias "schema".
    # Dump with ``by_alias=True`` to get the wire name.
    schema_: Literal["oa-result/1"] = Field(default="oa-result/1", alias="schema")
    id: str  # ULID
    status: Literal["ok", "warning", "failed_quality", "error"]
    summary_text: str = Field(alias="summary")
    items: tuple[ResultItem, ...]
    diagnostics: tuple[Diagnostic, ...] = ()
    provenance: Provenance

    summary_kind = "bundle"

    @model_validator(mode="after")
    def _unique_item_ids(self) -> ResultBundle:
        ids = [i.id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate ResultItem id in bundle")
        return self

    def _provenance_summary(self) -> JsonValue:
        p = self.provenance
        return {
            "analysis": p.analysis,
            "analysis_version": p.analysis_version,
            "solver": p.solver,
            "solver_version": p.solver_version,
            "inputs_hash": p.inputs_hash,
            "setup_hash": p.setup_hash,
            "board_hash": p.board_hash,
        }

    def _digest(self) -> str:
        return f"{self.provenance.analysis} {self.status}: {self.summary_text}"

    def _stats(self, max_items: int) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "status": self.status,
            "n_items": len(self.items),
            "items": [
                {"id": i.id, "title": i.title, "kind": i.kind, "digest": i.data.digest()}
                for i in self.items[:max_items]
            ],
            "diagnostics": [
                {"severity": d.severity, "code": d.code, "message": d.message}
                for d in self.diagnostics[:max_items]
            ],
        }
