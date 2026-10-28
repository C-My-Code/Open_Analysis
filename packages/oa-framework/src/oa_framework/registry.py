# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Plugin registry (interfaces.md section 3.1)."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from importlib.metadata import EntryPoint, entry_points
from typing import Any, TypeVar

from oa_framework.analysis import Analysis
from oa_framework.em import SolverAdapter
from oa_framework.errors import DuplicateRegistration, PluginLoadError, UnknownPlugin
from oa_framework.external import ExternalRunner
from oa_framework.ingest import Reader
from oa_framework.rules import Rule
from oa_framework.services import DictServices, Services

GROUP_INGEST = "open_analysis.ingest"
GROUP_ANALYSES = "open_analysis.analyses"
GROUP_RULES = "open_analysis.rules"
GROUP_SERVICES = "open_analysis.services"
GROUP_EM_ADAPTERS = "open_analysis.em_adapters"
GROUP_QS_ADAPTERS = "open_analysis.qs_adapters"
GROUP_RUNNERS = "open_analysis.runners"

ALL_GROUPS = (
    GROUP_INGEST,
    GROUP_ANALYSES,
    GROUP_RULES,
    GROUP_SERVICES,
    GROUP_EM_ADAPTERS,
    GROUP_QS_ADAPTERS,
    GROUP_RUNNERS,
)

EntryPointsProvider = Callable[[str], Iterable[EntryPoint]]

T = TypeVar("T")


def _default_provider(group: str) -> Iterable[EntryPoint]:
    return entry_points(group=group)


def _add(table: dict[str, T], group: str, name: str, value: T) -> None:
    if name in table:
        raise DuplicateRegistration(
            f"Duplicate {group} entry {name!r}",
            path=f"{group}/{name}",
            hint="Two installed packages register the same name; uninstall one of them.",
        )
    table[name] = value


class Registry:
    """Holds the discovered plugins. Build with :meth:`load` or :meth:`for_tests`."""

    def __init__(self) -> None:
        self.readers: dict[str, type[Reader]] = {}
        self.analyses: dict[str, type[Analysis]] = {}
        self.rules: dict[str, type[Rule]] = {}
        self.service_factories: dict[str, Callable[[], Any]] = {}
        self.em_adapters: dict[str, type[SolverAdapter]] = {}
        self.qs_adapters: dict[str, type[Any]] = {}
        self.runners: dict[str, type[ExternalRunner]] = {}
        self._services: DictServices | None = None

    # -- construction -------------------------------------------------------------------

    @classmethod
    def load(cls, provider: EntryPointsProvider | None = None) -> Registry:
        """Discover plugins through ``importlib.metadata.entry_points``.

        Duplicate names within a group are an error. ``provider(group)`` may be injected to
        supply entry points without installing packages.
        """
        get = provider if provider is not None else _default_provider
        reg = cls()
        tables: dict[str, dict[str, Any]] = {
            GROUP_INGEST: reg.readers,
            GROUP_ANALYSES: reg.analyses,
            GROUP_RULES: reg.rules,
            GROUP_SERVICES: reg.service_factories,
            GROUP_EM_ADAPTERS: reg.em_adapters,
            GROUP_QS_ADAPTERS: reg.qs_adapters,
            GROUP_RUNNERS: reg.runners,
        }
        for group, table in tables.items():
            seen: set[str] = set()
            for ep in get(group):
                if ep.name in seen:
                    raise DuplicateRegistration(
                        f"Duplicate {group} entry {ep.name!r}",
                        path=f"{group}/{ep.name}",
                        hint="Two installed packages register the same name; uninstall one.",
                    )
                seen.add(ep.name)
                try:
                    obj = ep.load()
                except Exception as exc:
                    raise PluginLoadError(
                        f"Cannot load {group} entry {ep.name!r} ({ep.value}): {exc}",
                        path=f"{group}/{ep.name}",
                        hint="Check that the providing package and its dependencies are installed.",
                    ) from exc
                table[ep.name] = obj
        return reg

    @classmethod
    def for_tests(
        cls,
        *,
        analyses: Sequence[type[Analysis]] = (),
        rules: Sequence[type[Rule]] = (),
        services: Mapping[str, Any] | None = None,
        readers: Sequence[type[Reader]] = (),
        em_adapters: Sequence[type[SolverAdapter]] = (),
        runners: Sequence[type[ExternalRunner]] = (),
    ) -> Registry:
        """A registry without entry points: pass the plugin classes/objects directly."""
        reg = cls()
        for a in analyses:
            _add(reg.analyses, GROUP_ANALYSES, a.kind, a)
        for r in rules:
            _add(reg.rules, GROUP_RULES, r.id, r)
        for rd in readers:
            _add(reg.readers, GROUP_INGEST, rd.name, rd)
        for ad in em_adapters:
            _add(reg.em_adapters, GROUP_EM_ADAPTERS, ad.name, ad)
        for rn in runners:
            _add(reg.runners, GROUP_RUNNERS, rn.name, rn)
        reg._services = DictServices(services or {})
        return reg

    # -- lookup -------------------------------------------------------------------------

    def analysis(self, kind: str) -> type[Analysis]:
        try:
            return self.analyses[kind]
        except KeyError:
            raise UnknownPlugin(
                f"Unknown analysis kind {kind!r}",
                path=f"{GROUP_ANALYSES}/{kind}",
                hint=f"Known kinds: {', '.join(sorted(self.analyses)) or 'none'}.",
            ) from None

    def rule(self, rule_id: str) -> type[Rule]:
        """Look up by id or by alias (e.g. ``E2`` for ``RP-001``)."""
        if rule_id in self.rules:
            return self.rules[rule_id]
        for r in self.rules.values():
            if rule_id in getattr(r, "aliases", ()):
                return r
        raise UnknownPlugin(
            f"Unknown rule {rule_id!r}",
            path=f"{GROUP_RULES}/{rule_id}",
            hint=f"Known rules: {', '.join(sorted(self.rules)) or 'none'}.",
        )

    def services(self) -> Services:
        """The service locator; entry-point factories are called lazily on first ``get``."""
        if self._services is None:
            self._services = DictServices(factories=self.service_factories)
        return self._services
