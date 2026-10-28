# SPDX-License-Identifier: GPL-3.0-or-later
"""Base class for every oa_model type."""

import warnings

from pydantic import BaseModel, ConfigDict

# The contracts name a field `schema` (e.g. `schema: Literal["oa-physics/1"]`); pydantic warns that
# it shadows the deprecated BaseModel.schema(). The shadowing is intended and harmless.
warnings.filterwarnings(
    "ignore",
    message=r'Field name "schema" in ".*" shadows an attribute in parent ".*"',
    category=UserWarning,
)

GEOM_EPS = 1e-9
"""Geometric tolerance in metres (1 nm, KiCad's internal resolution)."""


class OAModel(BaseModel):
    """Frozen, extra-forbidden pydantic model. Collections are tuples so models are deeply immutable."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        populate_by_name=True,
        use_enum_values=False,
        validate_default=True,
    )
