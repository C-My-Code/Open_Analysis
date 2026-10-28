# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The committed files under tests/fixtures match the builders."""

from __future__ import annotations

from pathlib import Path

import pytest
from oa_model.board import Board

from oa_testkit import fixtures_dump as fd


def test_write_then_check(tmp_path: Path) -> None:
    assert fd.main(["--root", str(tmp_path)]) == 0
    assert fd.main(["--root", str(tmp_path), "--check"]) == 0
    (tmp_path / "model" / "cpwg.oab.json").write_text("{}", encoding="utf-8")
    assert fd.main(["--root", str(tmp_path), "--check"]) == 1


@pytest.mark.parametrize("name", sorted(fd.BOARDS))
def test_model_fixture_round_trips(name: str, tmp_path: Path) -> None:
    fd.main(["--root", str(tmp_path)])
    text = (tmp_path / "model" / f"{name}.oab.json").read_text(encoding="utf-8")
    assert Board.model_validate_json(text) == fd.BOARDS[name]()


def test_committed_fixtures_are_current(update_golden: bool) -> None:
    root = fd.default_root()
    if update_golden:
        fd.main(["--root", str(root)])
    assert fd.main(["--root", str(root), "--check"]) == 0, (
        "tests/fixtures out of date; run `python -m oa_testkit.fixtures_dump` "
        "(or pytest --update-golden) and commit"
    )
