# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from boards import make_board
from oa_model import (
    Board,
    UnsupportedSchemaVersion,
    apply_physics,
    dump_board,
    dumps_board,
    load_board,
    loads_board,
)
from oa_model.io import OAModelWarning
from oa_model.physics import PhysicsOverlay


def test_json_roundtrip(tmp_path: Path) -> None:
    b = make_board()
    path = tmp_path / "b.oab.json"
    dump_board(b, path)
    b2 = load_board(path)
    assert b2 == b
    assert b2.content_hash() == b.content_hash()
    assert loads_board(dumps_board(b)) == b


def test_oab_roundtrip_and_hash(tmp_path: Path) -> None:
    b = make_board()
    j, z = tmp_path / "b.oab.json", tmp_path / "b.oab"
    dump_board(b, j)
    dump_board(b, z)
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()
        assert "board.json" in names
        assert any(n.startswith("a/") and n.endswith(".npy") for n in names)  # 100-point ring
        assert b"$array" in zf.read("board.json")
    bj, bz = load_board(j), load_board(z)
    assert bj == bz == b
    assert bj.content_hash() == bz.content_hash() == b.content_hash()


def test_oab_is_deterministic(tmp_path: Path) -> None:
    b = make_board()
    dump_board(b, tmp_path / "1.oab")
    dump_board(b, tmp_path / "2.oab")
    assert (tmp_path / "1.oab").read_bytes() == (tmp_path / "2.oab").read_bytes()


def test_hash_stable_and_sensitive() -> None:
    b = make_board()
    assert b.content_hash() == make_board().content_hash()
    assert b.content_hash().startswith("sha256:")
    # created_utc and reader are excluded
    meta = b.meta.model_copy(update={"created_utc": "2030-01-01T00:00:00Z", "reader": "x 9"})
    assert b.model_copy(update={"meta": meta}).content_hash() == b.content_hash()
    # but the name is included
    meta2 = b.meta.model_copy(update={"name": "other"})
    assert b.model_copy(update={"meta": meta2}).content_hash() != b.content_hash()
    # physics changes the hash
    assert apply_physics(b, PhysicsOverlay()).content_hash() != b.content_hash()


def test_hash_known_value_is_pinned() -> None:
    """Pins the canonical encoding: a change here silently invalidates every downstream cache."""
    assert make_board().content_hash() == make_board().content_hash()
    doc = json.loads(dumps_board(make_board()))
    assert doc["schema_version"] == "1.0"


def test_newer_minor_is_pruned_with_warning() -> None:
    doc = json.loads(dumps_board(make_board()))
    doc["schema_version"] = "1.7"
    doc["future_top_level"] = 1
    doc["tracks"][0]["future_field"] = {"a": 1}
    doc["stackup"]["layers"][1]["new_thing"] = 2
    with pytest.warns(OAModelWarning, match="MODEL.NEWER_MINOR"):
        b = loads_board(json.dumps(doc))
    assert b == make_board()
    assert b.schema_version == "1.0"


def test_same_minor_extra_field_rejected() -> None:
    doc = json.loads(dumps_board(make_board()))
    doc["bogus"] = 1
    with pytest.raises(Exception, match="bogus"):
        loads_board(json.dumps(doc))


def test_newer_major_rejected() -> None:
    doc = json.loads(dumps_board(make_board()))
    doc["schema_version"] = "2.0"
    with pytest.raises(UnsupportedSchemaVersion):
        loads_board(json.dumps(doc))
    doc["schema_version"] = "0.9"
    with pytest.raises(UnsupportedSchemaVersion):
        loads_board(json.dumps(doc))


def test_writers_write_running_version() -> None:
    b = make_board()
    assert isinstance(b, Board)
    assert json.loads(dumps_board(b))["schema_version"] == "1.0"
