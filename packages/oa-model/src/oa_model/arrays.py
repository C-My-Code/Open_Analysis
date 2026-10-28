# SPDX-License-Identifier: GPL-3.0-or-later
"""Immutable array primitives: Points, Floats, NDArray (board-model.md section 3)."""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import numpy.typing as npt
from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import core_schema


def _digest(a: npt.NDArray[Any], tag: bytes = b"") -> bytes:
    # `+ 0.0` turns -0.0 into 0.0 so the digest ignores the sign of zero.
    if a.dtype.kind == "f":
        a = a + 0.0
    return hashlib.sha256(tag + np.ascontiguousarray(a).tobytes()).digest()


class Points:
    """Immutable (N, 2) float64 array of XY coordinates in metres.

    JSON form: a flat list [x0, y0, x1, y1, ...] (even length), or {"$array": "<ref>"} inside a
    packed .oab container.
    """

    __slots__ = ("_a",)

    def __init__(self, data: Any) -> None:
        if isinstance(data, Points):
            data = data.array
        a = np.array(data, dtype=np.float64, copy=True)
        if a.ndim == 1:
            if a.size % 2:
                raise ValueError("flat point list must have even length")
            a = a.reshape(-1, 2)
        if a.size == 0:
            a = a.reshape(0, 2)
        if a.ndim != 2 or a.shape[1] != 2:
            raise ValueError(f"Points must be (N,2), got {a.shape}")
        if not np.isfinite(a).all():
            raise ValueError("Points contain NaN/Inf")
        a.setflags(write=False)
        self._a = a

    @property
    def array(self) -> npt.NDArray[np.float64]:
        """Read-only (N, 2) view."""
        return self._a

    def __len__(self) -> int:
        return int(self._a.shape[0])

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Points) and np.array_equal(self._a, other._a)

    def __hash__(self) -> int:
        return hash(self._a.tobytes())

    def __repr__(self) -> str:
        return f"Points(n={len(self)})"

    def digest(self) -> bytes:
        """SHA-256 over little-endian float64 bytes, used by canonical hashing."""
        return _digest(self._a.astype("<f8"))

    def to_flat_list(self) -> list[float]:
        return [float(v) for v in self._a.reshape(-1)]

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        def validate(v: Any) -> Points:
            return v if isinstance(v, Points) else Points(v)

        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda p: p.to_flat_list(), when_used="json"
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls, cs: core_schema.CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {
            "type": "array",
            "items": {"type": "number"},
            "description": "Flat XY list in metres: [x0,y0,x1,y1,...]; even length.",
            "examples": [[0.0, 0.0, 0.001, 0.0, 0.001, 0.001, 0.0, 0.001]],
        }


class Floats:
    """Immutable 1-D float64 array (frequency tables, Dk/Df tables)."""

    __slots__ = ("_a",)

    def __init__(self, data: Any) -> None:
        if isinstance(data, Floats):
            data = data.array
        a = np.array(data, dtype=np.float64, copy=True)
        if a.ndim != 1:
            raise ValueError(f"Floats must be 1-D, got {a.shape}")
        if not np.isfinite(a).all():
            raise ValueError("Floats contain NaN/Inf")
        a.setflags(write=False)
        self._a = a

    @property
    def array(self) -> npt.NDArray[np.float64]:
        return self._a

    def __len__(self) -> int:
        return int(self._a.shape[0])

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Floats) and np.array_equal(self._a, other._a)

    def __hash__(self) -> int:
        return hash(self._a.tobytes())

    def __repr__(self) -> str:
        return f"Floats(n={len(self)})"

    def digest(self) -> bytes:
        return _digest(self._a.astype("<f8"))

    def to_list(self) -> list[float]:
        return [float(v) for v in self._a]

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        def validate(v: Any) -> Floats:
            return v if isinstance(v, Floats) else Floats(v)

        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda p: p.to_list(), when_used="json"
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls, cs: core_schema.CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {
            "type": "array",
            "items": {"type": "number"},
            "description": "List of finite numbers (SI units of the owning field).",
        }


_DTYPES = {"f8": np.dtype("<f8"), "c16": np.dtype("<c16"), "i8": np.dtype("<i8")}


class NDArray:
    """Immutable N-D array with dtype f8, c16 or i8.

    JSON form: {"dtype", "shape", "data"}; complex data is interleaved re, im.
    """

    __slots__ = ("_a",)

    def __init__(self, data: Any, dtype: str | None = None) -> None:
        if isinstance(data, NDArray):
            data = data.array
        if dtype is not None:
            if dtype not in _DTYPES:
                raise ValueError(f"dtype must be one of {sorted(_DTYPES)}")
            a = np.array(data, dtype=_DTYPES[dtype], copy=True)
        else:
            a = np.array(data, copy=True)
            if a.dtype.kind == "f":
                a = a.astype("<f8")
            elif a.dtype.kind == "c":
                a = a.astype("<c16")
            elif a.dtype.kind in "iu":
                a = a.astype("<i8")
            else:
                raise ValueError(f"unsupported dtype {a.dtype}")
        if a.dtype.kind in "fc" and not np.isfinite(a).all():
            raise ValueError("NDArray contains NaN/Inf")
        a.setflags(write=False)
        self._a = a

    @property
    def array(self) -> npt.NDArray[Any]:
        return self._a

    @property
    def dtype_name(self) -> str:
        return {"f": "f8", "c": "c16", "i": "i8"}[self._a.dtype.kind]

    @property
    def shape(self) -> tuple[int, ...]:
        return tuple(int(s) for s in self._a.shape)

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, NDArray)
            and self._a.dtype == other._a.dtype
            and np.array_equal(self._a, other._a)
        )

    def __hash__(self) -> int:
        return hash((self._a.dtype.str, self._a.shape, self._a.tobytes()))

    def __repr__(self) -> str:
        return f"NDArray(dtype={self.dtype_name}, shape={self.shape})"

    def digest(self) -> bytes:
        tag = f"{self.dtype_name}{self.shape}".encode()
        return _digest(self._a, tag)

    def to_json_obj(self) -> dict[str, Any]:
        if self._a.dtype.kind == "c":
            flat = np.stack([self._a.real, self._a.imag], axis=-1).reshape(-1).tolist()
        else:
            flat = self._a.reshape(-1).tolist()
        return {"dtype": self.dtype_name, "shape": list(self.shape), "data": flat}

    @classmethod
    def from_json_obj(cls, obj: dict[str, Any]) -> NDArray:
        dtype = obj["dtype"]
        shape = tuple(int(s) for s in obj["shape"])
        # NDArray[Any]: float64 or int64 here, complex128 after the c16 branch below
        data: npt.NDArray[Any] = np.asarray(
            obj["data"], dtype=np.float64 if dtype != "i8" else np.int64
        )
        if dtype == "c16":
            data = data.reshape(-1, 2)
            data = data[:, 0] + 1j * data[:, 1]
        return cls(data.reshape(shape), dtype=dtype)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        def validate(v: Any) -> NDArray:
            if isinstance(v, NDArray):
                return v
            if isinstance(v, dict) and {"dtype", "shape", "data"} <= set(v):
                return NDArray.from_json_obj(v)
            return NDArray(v)

        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda p: p.to_json_obj(), when_used="json"
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(
        cls, cs: core_schema.CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {
            "type": "object",
            "properties": {
                "dtype": {"enum": ["f8", "c16", "i8"]},
                "shape": {"type": "array", "items": {"type": "integer"}},
                "data": {"type": "array", "items": {"type": "number"}},
            },
            "required": ["dtype", "shape", "data"],
            "description": "N-D array; row-major flat data, complex values interleaved re,im.",
        }
