# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tabular payload (interfaces.md section 4.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import JsonValue, model_validator

from oa_model._base import OAModel
from oa_results.artifact import ArtifactRef
from oa_results.container import PayloadBase


class ColumnSpec(OAModel):
    name: str
    dtype: Literal["f8", "i8", "str", "bool"]
    unit: str | None = None
    role: Literal["value", "id", "net", "layer", "item", "location_x", "location_y", "status"] = (
        "value"
    )


class TableResult(PayloadBase):
    kind: Literal["table"] = "table"
    columns: tuple[ColumnSpec, ...]
    # Parquet file. Small tables (< 1000 rows) may be inline only, so ``rows`` is optional when
    # ``inline`` is given (clarification of interfaces.md 4.1, see change request).
    rows: ArtifactRef | None = None
    inline: tuple[tuple[JsonValue, ...], ...] | None = None

    @model_validator(mode="after")
    def _has_data(self) -> TableResult:
        if self.rows is None and self.inline is None:
            raise ValueError("TableResult needs `rows` (Parquet artifact) or `inline` rows")
        if self.inline is not None:
            n = len(self.columns)
            if any(len(r) != n for r in self.inline):
                raise ValueError("inline row length does not match columns")
        return self

    def _digest(self) -> str:
        n = len(self.inline) if self.inline is not None else None
        rows = f"{n} row(s)" if n is not None else "rows in a Parquet artifact"
        names = ", ".join(c.name for c in self.columns[:6])
        return f"Table with {len(self.columns)} column(s) ({names}), {rows}."

    def _stats(self, max_items: int) -> dict[str, JsonValue]:
        stats: dict[str, JsonValue] = {
            "columns": [
                {"name": c.name, "dtype": c.dtype, "unit": c.unit, "role": c.role}
                for c in self.columns[:max_items]
            ],
            "n_rows": len(self.inline) if self.inline is not None else None,
            "artifact": self.rows.name if self.rows else None,
        }
        if self.inline is not None:
            stats["head"] = [list(r) for r in self.inline[: min(max_items, 5)]]
        return stats
