# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Common base of every result container: AI-friendly summaries, accuracy class, validity flags.

Every container exposes :meth:`ResultContainer.summary`, a size-bounded JSON-serializable dict
that includes a short plain-text ``digest``. Agents (and the MCP server) read summaries first
and fetch full arrays only on demand.
"""

from __future__ import annotations

import json
from abc import abstractmethod
from typing import Any, ClassVar, Literal

import numpy as np
from pydantic import JsonValue

from oa_model._base import OAModel

AccuracyClass = Literal["reference", "validated", "screening", "estimate", "unverified"]
"""How far the numbers can be trusted.

- ``reference``: field-solver or measured-quality result used as an oracle.
- ``validated``: method checked against the validation suite for this regime.
- ``screening``: fast closed-form or rule-based result, good for triage only.
- ``estimate``: heuristic or extrapolated result.
- ``unverified``: no accuracy statement available.
"""

DIGEST_MAX_CHARS = 240
SUMMARY_MAX_CHARS = 4000


class ValidityFlag(OAModel):
    """One machine-readable validity statement, e.g. ``quasi_static_fmax`` or ``extrapolated``."""

    code: str
    ok: bool
    message: str = ""


class SolverProvenance(OAModel):
    """Per-container provenance. The bundle-level ``Provenance`` carries the full job record."""

    solver: str | None = None
    solver_version: str | None = None
    inputs_hash: str | None = None  # sha256 over the canonical inputs that produced this container


def as_np(x: Any) -> np.ndarray[Any, Any]:
    """Read-only numpy view of an ``oa_model`` array primitive (or any array-like)."""
    return np.asarray(getattr(x, "array", x))


def value_span(x: Any) -> list[float] | None:
    """[min, max] of an array (magnitude for complex), or None when empty."""
    a = as_np(x)
    if a.size == 0:
        return None
    if np.iscomplexobj(a):
        a = np.abs(a)
    return [float(np.min(a)), float(np.max(a))]


def shape_of(x: Any) -> list[JsonValue]:
    return [int(n) for n in as_np(x).shape]


class ResultContainer(OAModel):
    """Base of all result containers."""

    accuracy_class: AccuracyClass | None = None
    validity_flags: tuple[ValidityFlag, ...] = ()

    summary_kind: ClassVar[str | None] = None

    @abstractmethod
    def _digest(self) -> str:
        """One or two plain-text sentences describing the content."""

    @abstractmethod
    def _stats(self, max_items: int) -> dict[str, JsonValue]:
        """Small JSON-serializable statistics. Lists must be capped at ``max_items``."""

    def _provenance_summary(self) -> JsonValue:
        return None

    def digest(self) -> str:
        """Short plain-text description, at most :data:`DIGEST_MAX_CHARS` characters."""
        text = " ".join(self._digest().split())
        if len(text) > DIGEST_MAX_CHARS:
            text = text[: DIGEST_MAX_CHARS - 3] + "..."
        return text

    def summary(
        self, *, max_chars: int = SUMMARY_MAX_CHARS, max_items: int = 20
    ) -> dict[str, JsonValue]:
        """Size-bounded, JSON-serializable summary.

        The serialized form is at most ``max_chars`` characters. When the statistics do not fit
        they are replaced by ``{"truncated": true}``; the digest, accuracy class and validity
        flags are always kept (the digest is shortened as a last resort).
        """
        kind = getattr(self, "kind", None) or self.summary_kind or type(self).__name__
        out: dict[str, JsonValue] = {
            "kind": kind,
            "digest": self.digest(),
            "accuracy_class": self.accuracy_class,
            "validity_flags": [f.model_dump(mode="json") for f in self.validity_flags[:max_items]],
            "provenance": self._provenance_summary(),
            "stats": self._stats(max_items),
        }
        # Shrink list caps before giving up on the statistics entirely.
        n = max_items
        while len(json.dumps(out)) > max_chars and n > 1:
            n = max(1, n // 2)
            out["stats"] = self._stats(n)
        if len(json.dumps(out)) > max_chars:
            out["stats"] = {"truncated": True}
        if len(json.dumps(out)) > max_chars:
            out["validity_flags"] = [f.model_dump(mode="json") for f in self.validity_flags[:3]]
            out["provenance"] = None
        if len(json.dumps(out)) > max_chars:
            out["digest"] = str(out["digest"])[: max(0, max_chars // 4)]
        return out


class PayloadBase(ResultContainer):
    """Base of the typed payloads inside a ``ResultItem`` (adds per-container provenance)."""

    provenance: SolverProvenance | None = None

    def _provenance_summary(self) -> JsonValue:
        return None if self.provenance is None else self.provenance.model_dump(mode="json")
