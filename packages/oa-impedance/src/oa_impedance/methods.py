# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Solver registration hook: ``oa_impedance.methods.register(name, solver_fn)``.

WP-202 plugs in ``"fem"`` through this module without editing WP-105 files. A solver function has
the signature ``solver_fn(xs, freqs) -> ImpedanceResult`` (``freqs`` is a tuple of Hz, never
empty). The built-in methods ``closed_form`` and ``bem`` are handled by the service itself;
``"fem"`` resolves lazily, so ``method="fem"`` raises :class:`MethodNotAvailable` until it is
registered.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from oa_model.xsection import CrossSection
from oa_results.rlgc import ImpedanceResult

from .errors import MethodNotAvailable

SolverFn = Callable[[CrossSection, Sequence[float]], ImpedanceResult]

_REGISTRY: dict[str, SolverFn] = {}

__all__ = ["SolverFn", "get", "names", "register", "unregister"]


def register(name: str, solver_fn: SolverFn, *, replace: bool = False) -> None:
    if name in ("auto", "closed_form", "bem"):
        raise ValueError(f"{name!r} is built in and cannot be replaced")
    if name in _REGISTRY and not replace:
        raise ValueError(f"solver {name!r} is already registered (pass replace=True)")
    _REGISTRY[name] = solver_fn


def unregister(name: str) -> None:
    _REGISTRY.pop(name, None)


def names() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def get(name: str) -> SolverFn:
    try:
        return _REGISTRY[name]
    except KeyError:
        hint = (
            "The FEM reference solver is added by WP-202 (oa_impedance.fem)."
            if name == "fem"
            else f"Registered methods: {', '.join(names()) or 'none'}."
        )
        raise MethodNotAvailable(
            f"impedance method {name!r} is not available",
            path="method",
            hint=hint + " Use method='bem' or 'closed_form'.",
        ) from None
