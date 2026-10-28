# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cross-analysis services (interfaces.md section 3.4)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, Protocol, runtime_checkable

from oa_model.xsection import CrossSection
from oa_results.rlgc import ImpedanceResult

RESERVED_SERVICE_NAMES = (
    "impedance",
    "via_model",
    "reference_map",
    "network",
    "qs_extract",
    "pdn",
)


@runtime_checkable
class ImpedanceService(Protocol):
    def solve(
        self,
        xs: CrossSection,
        freqs: Sequence[float] | None = None,
        *,
        method: Literal["auto", "closed_form", "bem", "fem"] = "auto",
    ) -> ImpedanceResult: ...

    def solve_many(
        self,
        xss: Sequence[CrossSection],
        freqs: Sequence[float] | None = None,
        *,
        method: str = "auto",
    ) -> list[ImpedanceResult]: ...

    def quick(self, xs: CrossSection) -> ImpedanceResult: ...


@runtime_checkable
class Services(Protocol):
    def get(self, name: str) -> Any:
        """Return the service; raises ``KeyError`` if absent."""
        ...

    def has(self, name: str) -> bool: ...


class DictServices:
    """``Services`` backed by a name -> object mapping, or lazy name -> factory mapping."""

    def __init__(
        self,
        services: Mapping[str, Any] | None = None,
        factories: Mapping[str, Callable[[], Any]] | None = None,
    ) -> None:
        self._services: dict[str, Any] = dict(services or {})
        self._factories: dict[str, Callable[[], Any]] = dict(factories or {})

    def get(self, name: str) -> Any:
        if name not in self._services:
            if name not in self._factories:
                raise KeyError(name)
            self._services[name] = self._factories[name]()
        return self._services[name]

    def has(self, name: str) -> bool:
        return name in self._services or name in self._factories

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(set(self._services) | set(self._factories)))
