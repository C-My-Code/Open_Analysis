# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from importlib.metadata import EntryPoint
from typing import Any, ClassVar, cast

import pytest

pytest.importorskip("oa_model")

from oa_framework import (
    DuplicateRegistration,
    ErrorInfo,
    NoReader,
    PluginLoadError,
    Registry,
    UnknownPlugin,
    to_error_info,
)
from oa_framework.analysis import Analysis
from oa_framework.rules import Rule
from oa_model._base import OAModel
from oa_model.errors import Cancelled


class _Cfg(OAModel):
    pass


class _DummyRule:
    id: ClassVar[str] = "RP-001"
    title: ClassVar[str] = "dummy"
    category: ClassVar[str] = "return_path"
    aliases: ClassVar[tuple[str, ...]] = ("E2",)
    default_severity: ClassVar[str] = "error"
    requires: ClassVar[frozenset[str]] = frozenset()
    references: ClassVar[tuple[str, ...]] = ()
    Config: ClassVar[type[OAModel]] = _Cfg

    def check(self, ctx: object, cfg: OAModel) -> list[object]:
        return []


class _DummyAnalysis:
    kind: ClassVar[str] = "dummy.kind"
    version: ClassVar[str] = "1"
    title: ClassVar[str] = "Dummy"
    Config: ClassVar[type[OAModel]] = _Cfg
    TaskPayload: ClassVar[type[OAModel]] = _Cfg

    def validate_setup(self, ctx: object, cfg: OAModel) -> tuple[()]:
        return ()

    def plan(self, ctx: object, cfg: OAModel) -> object:
        raise NotImplementedError

    def run_task(self, ctx: object, payload: OAModel) -> object:
        raise NotImplementedError

    def reduce(self, ctx: object, cfg: OAModel, outputs: object) -> object:
        raise NotImplementedError


def test_protocols_are_runtime_checkable() -> None:
    assert isinstance(_DummyRule(), Rule)
    assert isinstance(_DummyAnalysis(), Analysis)


def test_for_tests_lookup_and_alias() -> None:
    reg = Registry.for_tests(
        analyses=[_DummyAnalysis],  # type: ignore[list-item]
        rules=[_DummyRule],  # type: ignore[list-item]
        services={"impedance": object()},
    )
    assert cast(Any, reg.analysis("dummy.kind")) is _DummyAnalysis
    assert cast(Any, reg.rule("RP-001")) is _DummyRule
    assert cast(Any, reg.rule("E2")) is _DummyRule
    assert reg.services().has("impedance")
    assert not reg.services().has("pdn")
    with pytest.raises(KeyError):
        reg.services().get("pdn")


def test_unknown_plugin_is_structured() -> None:
    reg = Registry.for_tests()
    with pytest.raises(UnknownPlugin) as ei:
        reg.analysis("nope")
    info = ei.value.info()
    assert info.code == "REGISTRY_UNKNOWN"
    assert info.path == "open_analysis.analyses/nope"
    assert info.hint


def test_duplicate_in_for_tests() -> None:
    with pytest.raises(DuplicateRegistration):
        Registry.for_tests(rules=[_DummyRule, _DummyRule])  # type: ignore[list-item]


def test_load_from_entry_points_and_duplicates() -> None:
    ep = EntryPoint(
        name="RP-001", value="oa_framework.errors:NoReader", group="open_analysis.rules"
    )

    def provider(group: str) -> list[EntryPoint]:
        return [ep] if group == "open_analysis.rules" else []

    reg = Registry.load(provider)
    assert cast(Any, reg.rules["RP-001"]) is NoReader

    def dup(group: str) -> list[EntryPoint]:
        return [ep, ep] if group == "open_analysis.rules" else []

    with pytest.raises(DuplicateRegistration):
        Registry.load(dup)


def test_load_failure_is_structured() -> None:
    ep = EntryPoint(name="x", value="no_such_module_oa:Thing", group="open_analysis.rules")

    def provider(group: str) -> list[EntryPoint]:
        return [ep] if group == "open_analysis.rules" else []

    with pytest.raises(PluginLoadError) as ei:
        Registry.load(provider)
    assert to_error_info(ei.value).code == "REGISTRY_LOAD_FAILED"


def test_to_error_info_shapes() -> None:
    info = to_error_info(Cancelled("stopped"))
    assert info == ErrorInfo(code="CANCELLED", message="stopped")
    assert set(info.model_dump()) == {"code", "message", "path", "hint"}
    other = to_error_info(ValueError("boom"))
    assert other.code == "INTERNAL_ERROR" and "boom" in other.message
