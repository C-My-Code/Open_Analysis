# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Named scalar results (interfaces.md section 4.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import JsonValue

from oa_model._base import OAModel
from oa_results.container import PayloadBase


class Scalar(OAModel):
    name: str
    value: float
    unit: str | None = None
    status: Literal["pass", "warn", "fail"] | None = None
    limit: float | None = None


class ScalarSet(PayloadBase):
    kind: Literal["scalars"] = "scalars"
    values: tuple[Scalar, ...]

    def _digest(self) -> str:
        failing = [s.name for s in self.values if s.status == "fail"]
        text = f"{len(self.values)} scalar(s)"
        if failing:
            text += f"; failing: {', '.join(failing[:5])}"
        return text + "."

    def _stats(self, max_items: int) -> dict[str, JsonValue]:
        return {
            "n_values": len(self.values),
            "values": [s.model_dump(mode="json") for s in self.values[:max_items]],
        }
