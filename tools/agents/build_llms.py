# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate ``llms-full.txt``: ``llms.txt`` plus the full text of the agent-facing docs it links.

Only the sections in :data:`FULL_SECTIONS` are inlined, so unrelated doc edits do not make the
generated file stale. Run ``python tools/agents/build_llms.py`` (``--check`` fails when stale), and
``python tools/agents/check_docs.py`` for the link and tool-name checks.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FULL_SECTIONS = ("Agent guide", "MCP tools", "Accuracy classes")
_LINK = re.compile(r"\]\(([^)#]+)(?:#[^)]*)?\)")


def linked_docs(llms: str, sections: tuple[str, ...] = FULL_SECTIONS) -> list[str]:
    """Repo-relative paths linked from the named ``##`` sections, in order, deduplicated."""
    out: list[str] = []
    current = ""
    for line in llms.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
        elif current in sections:
            for target in _LINK.findall(line):
                if "://" not in target and target not in out:
                    out.append(target)
    return out


def build(root: Path = ROOT) -> str:
    """Text of ``llms-full.txt``."""
    llms = (root / "llms.txt").read_text(encoding="utf-8")
    parts = [llms.rstrip("\n"), ""]
    for rel in linked_docs(llms):
        body = (root / rel).read_text(encoding="utf-8").rstrip("\n")
        parts += ["", "---", "", f"<!-- {rel} -->", "", body]
    return "\n".join(parts) + "\n"


def main(argv: list[str]) -> int:
    """Write (default) or check ``llms-full.txt``."""
    target = ROOT / "llms-full.txt"
    text = build()
    if "--check" in argv:
        if not target.is_file() or target.read_text(encoding="utf-8") != text:
            print("llms-full.txt is stale; run python tools/agents/build_llms.py", file=sys.stderr)
            return 1
        return 0
    target.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {target} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
