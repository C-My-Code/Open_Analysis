# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Make ``profile_helpers`` importable under ``--import-mode=importlib``."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
