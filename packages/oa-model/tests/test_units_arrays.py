# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import TypeAdapter, ValidationError

from oa_model import Floats, NDArray, Points, Polygon, parse_quantity
from oa_model.common import Length
from oa_model.geometry import orient_ring, signed_area

EXAMPLES: list[tuple[str | float, str, float]] = [
    ("5mil", "length", 1.27e-4),
    ("0.2mm", "length", 2e-4),
    ("1 m", "length", 1.0),
    ("2.5um", "length", 2.5e-6),
    ("100nm", "length", 1e-7),
    ("1in", "length", 0.0254),
    ("3cm", "length", 0.03),
    ("1.6 GHz", "frequency", 1.6e9),
    ("100MHz", "frequency", 1e8),
    ("10kHz", "frequency", 1e4),
    ("50Hz", "frequency", 50.0),
    ("0.5mOhm", "resistance", 5e-4),
    ("50ohm", "resistance", 50.0),
    ("100R", "resistance", 100.0),
    ("4.7kOhm", "resistance", 4700.0),
    ("2MOhm", "resistance", 2e6),
    ("10nH", "inductance", 1e-8),
    ("100nF", "capacitance", 1e-7),
    ("4.7uF", "capacitance", 4.7e-6),
    ("2pF", "capacitance", 2e-12),
    ("12A", "current", 12.0),
    ("500mA", "current", 0.5),
    ("1.8V", "voltage", 1.8),
    ("20mV", "voltage", 0.02),
    ("25degC", "temperature", 298.15),
    ("300K", "temperature", 300.0),
    ("0degC", "temperature", 273.15),
    ("212degF", "temperature", 373.15),
    ("100ps", "time", 1e-10),
    ("2ns", "time", 2e-9),
    ("1ms", "time", 1e-3),
    ("3%", "fraction", 0.03),
    ("100ppm", "fraction", 1e-4),
    ("58MS/m", "conductivity", 5.8e7),
    ("2W", "power", 2.0),
    ("100mW", "power", 0.1),
    ("60deg", "angle", math.radians(60)),
    ("1.5", "length", 1.5),
    (0.25, "voltage", 0.25),
    ("-3mm", "length", -3e-3),
    ("1e-3m", "length", 1e-3),
]


@pytest.mark.parametrize(("text", "dim", "expected"), EXAMPLES)
def test_parse_examples(text: str | float, dim: str, expected: float) -> None:
    assert parse_quantity(text, dim) == pytest.approx(expected, rel=1e-12, abs=1e-30)


def test_five_mil_exact() -> None:
    assert parse_quantity("5mil", "length") == pytest.approx(1.27e-4)


@pytest.mark.parametrize(
    ("text", "dim"),
    [
        ("1oz", "length"),
        ("5GHz", "length"),
        ("3mm", "frequency"),
        ("abc", "length"),
        ("", "length"),
        ("5 furlongs", "length"),
        ("3%", "length"),
        ("10V", "current"),
        ("nan", "length"),
        ("1mm", "bogus"),
    ],
)
def test_parse_rejects(text: str, dim: str) -> None:
    with pytest.raises(ValueError):
        parse_quantity(text, dim)


def test_bool_and_inf_rejected() -> None:
    with pytest.raises(ValueError):
        parse_quantity(True, "length")
    with pytest.raises(ValueError):
        parse_quantity(float("inf"), "length")


def test_field_types_parse_strings() -> None:
    assert TypeAdapter(Length).validate_python("5mil") == pytest.approx(1.27e-4)
    with pytest.raises(ValidationError):
        TypeAdapter(Length).validate_python("1oz")
    with pytest.raises(ValidationError):
        TypeAdapter(Length).validate_python(-1.0)


_PREFIXES = {"": 1.0, "m": 1e-3, "u": 1e-6, "n": 1e-9, "k": 1e3}


@given(
    value=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    prefix=st.sampled_from(sorted(_PREFIXES)),
)
def test_parse_length_property(value: float, prefix: str) -> None:
    got = parse_quantity(f"{value!r}{prefix}m", "length")
    assert got == pytest.approx(value * _PREFIXES[prefix], rel=1e-12, abs=1e-30)


@given(st.floats(min_value=-200, max_value=1000, allow_nan=False))
def test_parse_celsius_property(c: float) -> None:
    assert parse_quantity(f"{c!r}degC", "temperature") == pytest.approx(c + 273.15, abs=1e-9)


@given(st.floats(min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False))
def test_bare_number_is_si(x: float) -> None:
    assert parse_quantity(x, "frequency") == x
    assert parse_quantity(repr(x), "frequency") == x


# ---- arrays ----------------------------------------------------------------------------------

_coords = st.lists(
    st.floats(min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    min_size=2,
    max_size=40,
).filter(lambda v: len(v) % 2 == 0)


@given(_coords)
def test_points_immutable(flat: list[float]) -> None:
    p = Points(flat)
    assert len(p) == len(flat) // 2
    assert not p.array.flags.writeable
    with pytest.raises(ValueError):
        p.array[0, 0] = 5.0
    # mutating the source list or array after construction must not change the Points
    arr = np.array(flat)
    q = Points(arr)
    arr[0] += 1.0
    assert q == Points(flat)
    assert hash(q) == hash(Points(flat))
    assert q.digest() == Points(flat).digest()


def test_points_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        Points([1.0, 2.0, 3.0])
    with pytest.raises(ValueError):
        Points([[1.0, 2.0, 3.0]])
    with pytest.raises(ValueError):
        Points([float("nan"), 0.0])


def test_floats_and_ndarray() -> None:
    f = Floats([1.0, 2.0])
    assert not f.array.flags.writeable
    c = NDArray(np.array([[1 + 2j, 3 - 1j]]))
    assert c.dtype_name == "c16"
    assert NDArray.from_json_obj(c.to_json_obj()) == c
    assert c.digest() != NDArray(np.array([[1 + 2j, 3 - 1j]]).T).digest()
    i = NDArray([1, 2, 3])
    assert i.dtype_name == "i8"
    assert not i.array.flags.writeable


@settings(max_examples=60)
@given(
    st.lists(
        st.tuples(st.floats(-1, 1, allow_nan=False), st.floats(-1, 1, allow_nan=False)),
        min_size=3,
        max_size=12,
        unique=True,
    ),
    st.booleans(),
)
def test_orient_ring_normalizes(pts: list[tuple[float, float]], want_ccw: bool) -> None:
    ring = Points(pts)
    out = orient_ring(ring, want_ccw)
    a = signed_area(out)
    if signed_area(ring) != 0.0:
        assert (a > 0) == want_ccw
    assert sorted(map(tuple, out.array.tolist())) == sorted(map(tuple, ring.array.tolist()))
    # idempotent
    assert orient_ring(out, want_ccw) == out


@given(
    st.lists(
        st.tuples(st.floats(-1, 1, allow_nan=False), st.floats(-1, 1, allow_nan=False)),
        min_size=3,
        max_size=10,
        unique=True,
    )
)
def test_polygon_normalized(pts: list[tuple[float, float]]) -> None:
    poly = Polygon(exterior=Points(pts), holes=(Points(pts[::-1]),))
    n = poly.normalized()
    if signed_area(n.exterior) != 0.0:
        assert signed_area(n.exterior) > 0
        assert signed_area(n.holes[0]) < 0
