# SPDX-License-Identifier: GPL-3.0-or-later
"""Load and dump `.oab.json` and `.oab` (board-model.md section 14.3)."""

from __future__ import annotations

import io
import json
import logging
import warnings
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePath
from typing import Any

import numpy as np
from pydantic import ValidationError

from oa_model.arrays import Floats, NDArray, Points
from oa_model.board import Board
from oa_model.common import Diagnostic
from oa_model.migrations import SCHEMA_MINOR, SCHEMA_VERSION, migrate, parse_version

_log = logging.getLogger("oa.model.io")

PACK_THRESHOLD = 64
"""In a `.oab` container, arrays with more elements than this go to .npy members."""


class OAModelWarning(UserWarning):
    """Warning category for non-fatal load findings such as MODEL.NEWER_MINOR."""


def _jsonable(obj: Any, pack: list[tuple[str, np.ndarray]] | None) -> Any:
    if isinstance(obj, Points | Floats):
        a = obj.array
        if pack is not None and a.size > PACK_THRESHOLD:
            name = f"a/{len(pack)}.npy"
            pack.append((name, a))
            return {"$array": name}
        return obj.to_flat_list() if isinstance(obj, Points) else obj.to_list()
    if isinstance(obj, NDArray):
        return obj.to_json_obj()
    if isinstance(obj, dict):
        return {str(k): _jsonable(v, pack) for k, v in obj.items()}
    if isinstance(obj, tuple | list):
        return [_jsonable(v, pack) for v in obj]
    if isinstance(obj, frozenset | set):
        return sorted(_jsonable(v, pack) for v in obj)
    if isinstance(obj, PurePath):
        return obj.as_posix()
    return obj


def _document(board: Board, pack: list[tuple[str, np.ndarray]] | None) -> dict[str, Any]:
    doc = _jsonable(board.model_dump(mode="python"), pack)
    doc["schema_version"] = SCHEMA_VERSION  # writers always write the running version
    return doc  # type: ignore[no-any-return]


def _text(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"


def dumps_board(board: Board) -> str:
    """Canonical `.oab.json` text."""
    return _text(_document(board, None))


def dump_board(board: Board, path: str | Path) -> None:
    """Write `board` to `path`. A `.oab` suffix writes the packed zip container; anything else
    (normally `.oab.json`) writes canonical JSON."""
    p = Path(path)
    if p.suffix == ".oab":
        pack: list[tuple[str, np.ndarray]] = []
        text = _text(_document(board, pack))
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
            zi = zipfile.ZipInfo("board.json", date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(zi, text.encode("utf-8"))
            for name, arr in pack:
                buf = io.BytesIO()
                np.lib.format.write_array(
                    buf, np.ascontiguousarray(arr, dtype="<f8"), version=(1, 0)
                )
                zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                zi.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(zi, buf.getvalue())
    else:
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(dumps_board(board))


def _resolve_arrays(obj: Any, zf: zipfile.ZipFile) -> Any:
    if isinstance(obj, dict):
        if set(obj) == {"$array"}:
            with zf.open(obj["$array"]) as fh:
                return np.lib.format.read_array(io.BytesIO(fh.read()), allow_pickle=False)
        return {k: _resolve_arrays(v, zf) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_arrays(v, zf) for v in obj]
    return obj


def _pointer(parts: Iterable[object]) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


_MISSING = object()


def _child(node: Any, seg: object) -> Any:
    if isinstance(node, dict):
        return node.get(seg, _MISSING)
    if isinstance(node, list) and isinstance(seg, int) and seg < len(node):
        return node[seg]
    return _MISSING


def _drop(data: Any, loc: tuple[Any, ...]) -> list[Any] | None:
    """Delete the key at `loc` from `data`, skipping union-tag segments absent from the data.
    Returns the data-path actually removed, or None when it cannot be located."""
    node: Any = data
    walked: list[Any] = []
    for seg in loc[:-1]:
        child = _child(node, seg)
        if child is _MISSING:
            continue
        node = child
        walked.append(seg)
    last = loc[-1]
    if isinstance(node, dict) and last in node:
        del node[last]
        return [*walked, last]
    return None


def board_from_dict(data: dict[str, Any]) -> tuple[Board, tuple[Diagnostic, ...]]:
    """Validate a board document: migrate older majors, prune newer-minor fields with a
    MODEL.NEWER_MINOR warning, reject newer majors."""
    data = migrate(dict(data))
    _, minor = parse_version(data.get("schema_version"))
    diags: list[Diagnostic] = []
    dropped: list[str] = []
    while True:
        try:
            board = Board.model_validate(data)
            break
        except ValidationError as exc:
            errors = exc.errors()
            if minor <= SCHEMA_MINOR or not all(e["type"] == "extra_forbidden" for e in errors):
                raise
            progressed = False
            for e in sorted(errors, key=lambda e: [str(x) for x in e["loc"]], reverse=True):
                removed = _drop(data, tuple(e["loc"]))
                if removed is not None:
                    dropped.append(_pointer(removed))
                    progressed = True
            if not progressed:
                raise
    if dropped:
        board = board.model_copy(update={"schema_version": SCHEMA_VERSION})
        diags.append(
            Diagnostic(
                severity="warning",
                code="MODEL.NEWER_MINOR",
                message=(
                    f"document schema_version {data.get('schema_version')} is newer than "
                    f"{SCHEMA_VERSION}; dropped {len(dropped)} unknown field(s): "
                    + ", ".join(sorted(dropped))
                ),
                hint="upgrade oa-model to read these fields",
            )
        )
    return board, tuple(diags)


def loads_board(text: str) -> Board:
    """Parse `.oab.json` text."""
    board, diags = board_from_dict(json.loads(text))
    _emit(diags)
    return board


def _emit(diags: tuple[Diagnostic, ...]) -> None:
    for d in diags:
        _log.warning("%s: %s", d.code, d.message)
        warnings.warn(f"{d.code}: {d.message}", OAModelWarning, stacklevel=3)


def load_board(path: str | Path) -> Board:
    """Load a `.oab.json` or `.oab` file (format sniffed from the content)."""
    p = Path(path)
    raw = p.read_bytes()
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            doc = _resolve_arrays(json.loads(zf.read("board.json").decode("utf-8")), zf)
    else:
        doc = json.loads(raw.decode("utf-8"))
    board, diags = board_from_dict(doc)
    _emit(diags)
    return board
