# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Copy the agent docs into ``oa_mcp/data`` so the installed server can serve them as resources.

Run ``python tools/agents/sync_docs.py`` after editing ``docs/agents/*`` or ``llms.txt``. A test
in ``packages/oa-mcp`` fails when the copies drift.
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "packages" / "oa-mcp" / "src" / "oa_mcp" / "data"

#: bundled name -> source file (relative to the repo root)
SOURCES: dict[str, str] = {
    "agent-guide.md": "docs/agents/AGENTS.md",
    "accuracy.md": "docs/agents/accuracy.md",
    "llms.txt": "llms.txt",
}


def main() -> None:
    """Copy every source into the package data directory."""
    DATA.mkdir(parents=True, exist_ok=True)
    for name, src in SOURCES.items():
        shutil.copyfile(ROOT / src, DATA / name)
        print(f"{src} -> {DATA / name}")


if __name__ == "__main__":
    main()
