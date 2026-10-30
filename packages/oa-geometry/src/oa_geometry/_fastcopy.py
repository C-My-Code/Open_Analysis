# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``replace``: ``model.model_copy(update=...)`` without pydantic's generic copy machinery.

WP-515: the per-sample cross-section construction copies pre-validated models tens of
thousands of times per board; ``BaseModel.__copy__`` dominates. The result is identical to
``model_copy(update=...)`` (no validation, ``model_fields_set`` extended by the updated keys)
for models without private attributes or extras, and falls back to it otherwise.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)

_set = object.__setattr__


def replace(model: M, **update: Any) -> M:
    if model.__pydantic_private__ is not None or model.__pydantic_extra__ is not None:
        return model.model_copy(update=update)
    cls = model.__class__
    new = cls.__new__(cls)
    d = model.__dict__.copy()
    d.update(update)
    _set(new, "__dict__", d)
    _set(new, "__pydantic_extra__", None)
    _set(new, "__pydantic_fields_set__", model.__pydantic_fields_set__ | update.keys())
    _set(new, "__pydantic_private__", None)
    return new
