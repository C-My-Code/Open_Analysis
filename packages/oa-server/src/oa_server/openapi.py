# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Export the OpenAPI document: ``python -m oa_server.openapi [--check] [PATH]``.

The committed file is ``web/src/api/openapi.json`` (WP-209 generates its TypeScript client from
it); CI regenerates it and fails on any diff (``--check``).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from oa_server.app import create_app

__all__ = ["build_openapi", "main", "render"]

DEFAULT_PATH = Path("web/src/api/openapi.json")


def build_openapi() -> dict[str, Any]:
    doc: dict[str, Any] = create_app(None).openapi()
    return doc


def render() -> str:
    return json.dumps(build_openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m oa_server.openapi")
    ap.add_argument("path", nargs="?", type=Path, default=DEFAULT_PATH)
    ap.add_argument("--check", action="store_true", help="fail if the file is out of date")
    ns = ap.parse_args(argv)
    text = render()
    if ns.check:
        if not ns.path.is_file() or ns.path.read_text(encoding="utf-8") != text:
            print(f"{ns.path} is out of date; run: python -m oa_server.openapi")
            return 1
        return 0
    ns.path.parent.mkdir(parents=True, exist_ok=True)
    ns.path.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {ns.path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
