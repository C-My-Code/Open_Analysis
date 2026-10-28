# SPDX-License-Identifier: GPL-3.0-or-later
"""The committed schemas/*.json match the models, and every field is self-documenting."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path

import oa_model
from oa_model import OAModel
from oa_model.schemas import SCHEMA_MODELS, generate

REPO = Path(__file__).resolve().parents[3]
SCHEMAS = REPO / "schemas"


def test_committed_schemas_match_models() -> None:
    assert SCHEMAS.is_dir(), (
        "schemas/ directory missing; run `python -m oa_model.schemas --out schemas`"
    )
    stale = []
    for name, text in generate().items():
        f = SCHEMAS / name
        if not f.exists() or f.read_bytes().decode("utf-8") != text:
            stale.append(name)
    assert not stale, f"stale schema files {stale}; run `python -m oa_model.schemas --out schemas`"


def test_schema_files_carry_license_comment() -> None:
    for name in SCHEMA_MODELS:
        assert "CC-BY-4.0" in (SCHEMAS / name).read_text(encoding="utf-8")
    assert "CC-BY-4.0" in (SCHEMAS / "CHANGELOG.md").read_text(encoding="utf-8")


def _all_models() -> list[type[OAModel]]:
    out: list[type[OAModel]] = []
    for info in pkgutil.walk_packages(oa_model.__path__, "oa_model."):
        mod = importlib.import_module(info.name)
        for _, cls in inspect.getmembers(mod, inspect.isclass):
            if issubclass(cls, OAModel) and cls.__module__ == mod.__name__ and cls is not OAModel:
                out.append(cls)
    return out


def test_every_field_has_a_description() -> None:
    missing = []
    models = _all_models()
    assert len(models) > 80
    for cls in models:
        if cls.__name__.startswith("_"):
            continue
        for fname, finfo in cls.model_fields.items():
            if not finfo.description:
                missing.append(f"{cls.__name__}.{fname}")
    assert not missing, f"fields without Field(description=...): {missing}"


def test_generated_schema_has_descriptions() -> None:
    schema = SCHEMA_MODELS["board-model.v1.json"].model_json_schema()
    track = schema["$defs"]["Track"]["properties"]
    assert "metres" in track["width"]["description"]
    assert "Diagnostic" in schema["$defs"]
    assert "hint" in schema["$defs"]["Diagnostic"]["properties"]
    assert "path" in schema["$defs"]["Diagnostic"]["properties"]
