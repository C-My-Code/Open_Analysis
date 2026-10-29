# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared constants for the oa-mcp tests."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BOARD_DIR = REPO_ROOT / "tests" / "boards" / "kicad10" / "microstrip"
BOARD_FILE = REPO_ROOT / "tests" / "fixtures" / "model" / "microstrip.oab.json"
