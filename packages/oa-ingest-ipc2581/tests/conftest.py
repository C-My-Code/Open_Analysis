# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

REPO = Path(__file__).resolve().parents[3]
DATA = Path(__file__).parent / "data"
SYNTHETIC = REPO / "tests" / "boards" / "kicad10"
