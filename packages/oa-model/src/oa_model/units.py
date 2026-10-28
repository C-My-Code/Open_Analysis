# SPDX-License-Identifier: GPL-3.0-or-later
"""SI unit constants and `parse_quantity` (board-model.md section 2)."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import Any

M = 1.0
MM = 1e-3
UM = 1e-6
NM = 1e-9
MIL = 25.4e-6
INCH = 25.4e-3
OZ_CU = 34.79e-6  # 1 oz/ft^2 copper thickness
HZ = 1.0
KHZ = 1e3
MHZ = 1e6
GHZ = 1e9
DEG = 3.141592653589793 / 180.0
ZERO_C = 273.15

DIMENSIONS = (
    "length",
    "frequency",
    "resistance",
    "inductance",
    "capacitance",
    "current",
    "voltage",
    "temperature",
    "time",
    "fraction",
    "conductivity",
    "power",
    "angle",
)

_PREFIX = {
    "f": 1e-15,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "μ": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
    "T": 1e12,
}
_LENGTH_PREFIX = {**_PREFIX, "c": 1e-2}

# dimension -> (base units, specials without prefix, prefix table)
_Spec = tuple[dict[str, float], dict[str, float], dict[str, float]]
_SPECS: dict[str, _Spec] = {
    "length": (
        {"m": 1.0},
        {"mil": MIL, "mils": MIL, "thou": MIL, "in": INCH, "inch": INCH, '"': INCH},
        _LENGTH_PREFIX,
    ),
    "frequency": ({"hz": 1.0}, {}, _PREFIX),
    "resistance": (
        {"ohm": 1.0, "ohms": 1.0, "ω": 1.0, "r": 1.0},
        {},
        _PREFIX,
    ),
    "inductance": ({"h": 1.0}, {}, _PREFIX),
    "capacitance": ({"f": 1.0}, {}, _PREFIX),
    "current": ({"a": 1.0}, {}, _PREFIX),
    "voltage": ({"v": 1.0}, {}, _PREFIX),
    "time": ({"s": 1.0}, {"min": 60.0, "h": 3600.0, "hr": 3600.0}, _PREFIX),
    "power": ({"w": 1.0}, {}, _PREFIX),
    "conductivity": ({"s/m": 1.0, "s/mm": 1e3, "s/cm": 1e2}, {}, _PREFIX),
    "fraction": ({}, {"%": 0.01, "ppm": 1e-6, "ppb": 1e-9}, {}),
    "angle": ({"rad": 1.0}, {"deg": DEG, "°": DEG, "degrees": DEG}, _PREFIX),
}
# temperature units: unit -> (scale, offset) with kelvin = value * scale + offset
_TEMPERATURE = {
    "k": (1.0, 0.0),
    "kelvin": (1.0, 0.0),
    "degc": (1.0, ZERO_C),
    "°c": (1.0, ZERO_C),
    "c": (1.0, ZERO_C),
    "celsius": (1.0, ZERO_C),
    "degf": (5.0 / 9.0, ZERO_C - 32.0 * 5.0 / 9.0),
    "°f": (5.0 / 9.0, ZERO_C - 32.0 * 5.0 / 9.0),
    "f": (5.0 / 9.0, ZERO_C - 32.0 * 5.0 / 9.0),
}

_NUM = re.compile(r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*(.*?)\s*$")


def _unit_factor(unit: str, dimension: str) -> float:
    bases, specials, prefixes = _SPECS[dimension]
    low = unit.lower()
    if low in specials:
        return specials[low]
    if low in bases:
        return bases[low]
    if len(unit) > 1 and unit[0] in prefixes and unit[1:].lower() in bases:
        return prefixes[unit[0]] * bases[unit[1:].lower()]
    raise ValueError(f"unit {unit!r} is not valid for dimension {dimension!r}")


def parse_quantity(text: str | float, dimension: str) -> float:
    """Parse '0.2mm', '5mil', '1.6 GHz', '3%', '25degC', '0.5mOhm' into SI floats.

    `dimension` is one of DIMENSIONS. A bare number is taken as already SI. 'm' alone always
    means metre; milli needs a unit after it (mm, mOhm, mA). Raises ValueError on a dimension
    mismatch or an unparseable string.
    """
    if dimension not in DIMENSIONS:
        raise ValueError(f"unknown dimension {dimension!r}")
    if isinstance(text, bool):
        raise ValueError("a bool is not a quantity")
    if isinstance(text, int | float):
        value = float(text)
        if not math.isfinite(value):
            raise ValueError("quantity must be finite")
        return value
    if not isinstance(text, str):
        raise ValueError(f"cannot parse quantity from {type(text).__name__}")
    match = _NUM.match(text)
    if match is None:
        raise ValueError(f"cannot parse quantity {text!r}")
    number = float(match.group(1))
    unit = match.group(2)
    if not unit:
        result = number
    elif dimension == "temperature":
        if unit.lower() not in _TEMPERATURE:
            raise ValueError(f"unit {unit!r} is not valid for dimension 'temperature'")
        scale, offset = _TEMPERATURE[unit.lower()]
        result = number * scale + offset
    else:
        result = number * _unit_factor(unit, dimension)
    if not math.isfinite(result):
        raise ValueError("quantity must be finite")
    return result


def quantity(dimension: str) -> Callable[[Any], Any]:
    """Build a pydantic BeforeValidator function that parses strings like '5mil' and passes
    numbers through unchanged."""

    def _validate(value: Any) -> Any:
        if isinstance(value, str):
            return parse_quantity(value, dimension)
        return value

    return _validate
