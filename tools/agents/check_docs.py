# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Link and tool-name checker for the agent docs (ai-integration section 7.3).

Checks that every relative link in ``llms.txt``, the agent docs, ``CLAUDE.md`` and ``AGENTS.md``
resolves, and that every tool named as ``tool(...)`` or listed on the ``- Tools:`` line of
``llms.txt`` is an MCP tool of the catalog (``oa_mcp.contract.TOOLS`` plus the registry's tools
when a registry is given).
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ("llms.txt", "docs/agents/AGENTS.md", "docs/agents/connect.md", "CLAUDE.md", "AGENTS.md")
_LINK = re.compile(r"\]\(([^)#]+)(?:#[^)]*)?\)")


def broken_links(root: Path = ROOT, docs: Iterable[str] = DOCS) -> list[str]:
    """``doc: target`` for every relative link that does not resolve."""
    bad: list[str] = []
    for rel in docs:
        path = root / rel
        for target in _LINK.findall(path.read_text(encoding="utf-8")):
            if "://" not in target and not (path.parent / target).exists():
                bad.append(f"{rel}: {target}")
    return bad


def unknown_tools(tools: Iterable[str], root: Path = ROOT) -> list[str]:
    """Tool names used in the docs that are not in ``tools``."""
    known = set(tools)
    bad: list[str] = []
    guide = (root / "docs/agents/AGENTS.md").read_text(encoding="utf-8")
    for name in sorted(set(re.findall(r"`([a-z_]+)\(", guide))):
        if name not in known:
            bad.append(f"docs/agents/AGENTS.md: {name}")
    llms = (root / "llms.txt").read_text(encoding="utf-8")
    m = re.search(r"^- Tools: (.+)\.$", llms, re.MULTILINE)
    listed = m.group(1).split(", ") if m else []
    bad += [f"llms.txt: {n}" for n in listed if n not in known]
    bad += [f"llms.txt: missing {n}" for n in sorted(known) if n not in listed and m is None]
    return bad


def main() -> int:
    """CLI entry: checks against the static MCP contract."""
    sys.path.insert(0, str(ROOT / "packages" / "oa-mcp" / "src"))
    from oa_mcp.contract import TOOLS

    problems = broken_links() + unknown_tools(TOOLS)
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
