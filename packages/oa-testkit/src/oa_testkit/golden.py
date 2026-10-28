# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Golden-data helpers.

A golden case lives in ``tests/golden/<suite>/<case>/case.toml``::

    source = "analytic: Z0 = eta0/(2 pi sqrt(er)) ln(b/a)"   # citation, or "analytic: <formula>"

    [tolerance]
    rtol = 1e-4
    atol = 0.0
    [tolerance.per_key.z0]      # optional per-key override
    rtol = 1e-6

    [inputs]
    ln_b_over_a = 1.0

    [expected]
    z0 = 59.958

``assert_close_to_golden(actual, suite, case)`` compares ``actual`` (a possibly nested mapping of
numbers, strings, bools and arrays) with ``[expected]`` using ``|a - e| <= atol + rtol * |e|``.
Complex data is compared as ``{"re": ..., "im": ...}`` mappings.

Running ``pytest --update-golden`` rewrites ``[expected]`` from the actual values instead of
comparing. The file is regenerated as a whole, so comments in it are lost; keep prose in
``source``. Review and list regenerated files in the PR description.

This module has no dependency on ``oa_model`` and is safe to import from the pytest plugin.
"""

from __future__ import annotations

import json
import math
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

__all__ = [
    "GoldenCase",
    "GoldenError",
    "Tolerance",
    "assert_close_to_golden",
    "golden_root",
    "load_case",
    "set_update_mode",
    "update_mode",
    "write_case",
]

_UPDATE = False


class GoldenError(AssertionError):
    """A golden comparison failed or a case file is malformed."""


def set_update_mode(value: bool) -> None:
    """Called by the pytest plugin from ``--update-golden``."""
    global _UPDATE
    _UPDATE = bool(value)


def update_mode() -> bool:
    return _UPDATE


def golden_root() -> Path:
    """Directory holding the golden suites.

    ``$OA_GOLDEN_DIR`` wins; otherwise the nearest ancestor of the current directory that has a
    ``tests/golden`` directory or, failing that, a workspace root (``pyproject.toml`` with
    ``[tool.uv.workspace]``), whose ``tests/golden`` is used.
    """
    env = os.environ.get("OA_GOLDEN_DIR")
    if env:
        return Path(env)
    cwd = Path.cwd().resolve()
    for d in (cwd, *cwd.parents):
        if (d / "tests" / "golden").is_dir():
            return d / "tests" / "golden"
    for d in (cwd, *cwd.parents):
        pp = d / "pyproject.toml"
        if pp.is_file() and "[tool.uv.workspace]" in pp.read_text(encoding="utf-8"):
            return d / "tests" / "golden"
    return cwd / "tests" / "golden"


@dataclass(frozen=True)
class Tolerance:
    rtol: float = 1e-9
    atol: float = 0.0
    per_key: Mapping[str, Tolerance] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any]) -> Tolerance:
        per_key = {k: cls.from_mapping(v) for k, v in dict(m.get("per_key", {})).items()}
        return cls(float(m.get("rtol", 1e-9)), float(m.get("atol", 0.0)), per_key)

    def to_mapping(self) -> dict[str, Any]:
        out: dict[str, Any] = {"rtol": self.rtol, "atol": self.atol}
        if self.per_key:
            out["per_key"] = {k: v.to_mapping() for k, v in self.per_key.items()}
        return out


@dataclass(frozen=True)
class GoldenCase:
    suite: str
    name: str
    path: Path
    source: str
    tolerance: Tolerance
    inputs: Mapping[str, Any]
    expected: Mapping[str, Any]


def _case_path(suite: str, case: str, root: Path | None) -> Path:
    return (Path(root) if root is not None else golden_root()) / suite / case / "case.toml"


def load_case(suite: str, case: str, *, root: Path | None = None) -> GoldenCase:
    path = _case_path(suite, case, root)
    if not path.is_file():
        raise GoldenError(f"golden case not found: {path} (create it with --update-golden)")
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    missing = [k for k in ("source", "tolerance", "inputs", "expected") if k not in data]
    if missing:
        raise GoldenError(f"{path}: missing required keys {missing}")
    if not str(data["source"]).strip():
        raise GoldenError(f"{path}: 'source' must cite a reference or 'analytic: <formula>'")
    return GoldenCase(
        suite,
        case,
        path,
        str(data["source"]),
        Tolerance.from_mapping(data["tolerance"]),
        data["inputs"],
        data["expected"],
    )


# ---------------------------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------------------------


def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float, np.integer, np.floating)) and not isinstance(x, bool)


def _compare(actual: Any, expected: Any, tol: Tolerance, path: str, errs: list[str]) -> None:
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            errs.append(f"{path}: expected a mapping, got {type(actual).__name__}")
            return
        for key, ev in expected.items():
            sub = tol.per_key.get(key, tol)
            if key not in actual:
                errs.append(f"{path}.{key}: missing in actual")
                continue
            _compare(actual[key], ev, sub, f"{path}.{key}", errs)
        return
    if isinstance(expected, list) or isinstance(actual, (np.ndarray, list, tuple)):
        try:
            a = np.asarray(actual, dtype=float)
            e = np.asarray(expected, dtype=float)
        except (TypeError, ValueError):
            if list(actual) != list(expected):
                errs.append(f"{path}: {actual!r} != {expected!r}")
            return
        if a.shape != e.shape:
            errs.append(f"{path}: shape {a.shape} != expected {e.shape}")
            return
        bad = ~(np.abs(a - e) <= tol.atol + tol.rtol * np.abs(e))
        if bad.any():
            i = tuple(int(v) for v in np.argwhere(bad)[0])
            errs.append(
                f"{path}: {int(bad.sum())}/{a.size} elements outside tolerance "
                f"(rtol={tol.rtol:g}, atol={tol.atol:g}); first at {i}: "
                f"actual {a[i]!r} vs expected {e[i]!r}"
            )
        return
    if _is_number(expected):
        if not _is_number(actual):
            errs.append(f"{path}: expected a number, got {actual!r}")
            return
        a, e = float(actual), float(expected)
        ok = math.isnan(a) if math.isnan(e) else abs(a - e) <= tol.atol + tol.rtol * abs(e)
        if not ok:
            rel = abs(a - e) / abs(e) if e else float("inf")
            errs.append(
                f"{path}: actual {a!r} vs expected {e!r} (rel diff {rel:.3g}, "
                f"rtol={tol.rtol:g}, atol={tol.atol:g})"
            )
        return
    if actual != expected:
        errs.append(f"{path}: {actual!r} != {expected!r}")


def assert_close_to_golden(
    actual: Mapping[str, Any],
    suite: str,
    case: str,
    *,
    root: Path | None = None,
    source: str | None = None,
    inputs: Mapping[str, Any] | None = None,
    rtol: float | None = None,
    atol: float | None = None,
    update: bool | None = None,
) -> None:
    """Compare ``actual`` with the ``[expected]`` table of ``<suite>/<case>/case.toml``.

    ``rtol``/``atol`` override the file's ``[tolerance]`` defaults for this call. In update mode
    (``--update-golden`` or ``update=True``) the file is written instead of compared; creating a
    new case then requires ``source``.
    """
    do_update = update_mode() if update is None else update
    path = _case_path(suite, case, root)
    if do_update:
        if path.is_file():
            old = load_case(suite, case, root=root)
            src, tol, inp = old.source, old.tolerance, dict(old.inputs)
            if source is not None:
                src = source
            if inputs is not None:
                inp = dict(inputs)
        else:
            if not source:
                raise GoldenError(
                    f"creating {path} requires source=... (citation or 'analytic: ...')"
                )
            src, tol, inp = source, Tolerance(), dict(inputs or {})
        if rtol is not None or atol is not None:
            tol = Tolerance(
                tol.rtol if rtol is None else rtol, tol.atol if atol is None else atol, tol.per_key
            )
        write_case(path, source=src, tolerance=tol, inputs=inp, expected=actual)
        return

    gc = load_case(suite, case, root=root)
    tol = gc.tolerance
    if rtol is not None or atol is not None:
        tol = Tolerance(
            tol.rtol if rtol is None else rtol, tol.atol if atol is None else atol, tol.per_key
        )
    errs: list[str] = []
    _compare(actual, gc.expected, tol, "expected", errs)
    if errs:
        raise GoldenError(
            f"golden mismatch for {suite}/{case} ({gc.path}); source: {gc.source}\n  "
            + "\n  ".join(errs)
            + "\nIf the change is intended, regenerate with `pytest --update-golden` and review."
        )


# ---------------------------------------------------------------------------------------------
# Minimal TOML writer (tomllib reads only)
# ---------------------------------------------------------------------------------------------

_BARE = re.compile(r"^[A-Za-z0-9_-]+$")


def _key(k: str) -> str:
    return k if _BARE.match(k) else json.dumps(k)


def _plain(v: Any) -> Any:
    """Convert numpy / tuples / complex into TOML-representable Python values."""
    if isinstance(v, Mapping):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, np.ndarray):
        if np.iscomplexobj(v):
            return {"re": _plain(v.real), "im": _plain(v.imag)}
        return _plain(v.tolist())
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, complex):
        return {"re": v.real, "im": v.imag}
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, Path):
        return v.as_posix()
    return v


def _val(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        if math.isinf(v):
            return "inf" if v > 0 else "-inf"
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(_val(x) for x in v) + "]"
    if isinstance(v, Mapping):
        return "{" + ", ".join(f"{_key(k)} = {_val(x)}" for k, x in v.items()) + "}"
    raise GoldenError(f"cannot write {type(v).__name__} value {v!r} to TOML")


def _emit_table(prefix: str, table: Mapping[str, Any], out: list[str]) -> None:
    scalars = {k: v for k, v in table.items() if not isinstance(v, Mapping)}
    tables = {k: v for k, v in table.items() if isinstance(v, Mapping)}
    if prefix and (scalars or not tables):
        out.append(f"[{prefix}]")
    for k, v in scalars.items():
        out.append(f"{_key(k)} = {_val(v)}")
    if scalars or (prefix and not tables):
        out.append("")
    for k, v in tables.items():
        _emit_table(f"{prefix}.{_key(k)}" if prefix else _key(k), v, out)


def dumps_toml(data: Mapping[str, Any]) -> str:
    out: list[str] = []
    _emit_table("", _plain(data), out)
    return "\n".join(out).rstrip() + "\n"


def write_case(
    path: Path,
    *,
    source: str,
    tolerance: Tolerance,
    inputs: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> None:
    """Write a ``case.toml`` (used by ``--update-golden`` and by fixture generators)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = dumps_toml(
        {
            "source": source,
            "tolerance": tolerance.to_mapping(),
            "inputs": dict(inputs),
            "expected": dict(expected),
        }
    )
    tomllib.loads(text)  # fail loudly rather than write a file we cannot read back
    path.write_text(text, encoding="utf-8", newline="\n")
