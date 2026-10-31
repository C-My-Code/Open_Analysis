# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The ``oa-mcp`` console script (section 8.1)."""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from oa_mcp.config import read_mcp_table, resolve_settings
from oa_mcp.http import load_or_create_token, run_http
from oa_mcp.server import build_server
from oa_mcp.stdio import run_stdio
from oa_service import OAService, Registry

DEFAULT_FACTORY = "oa_service.local:LocalService"


def build_parser() -> argparse.ArgumentParser:
    """The argument parser (kept separate so tests and docs can introspect it)."""
    p = argparse.ArgumentParser(
        prog="oa-mcp", description="MCP server for Open_Analysis (stdio by default)."
    )
    p.add_argument("--root", action="append", metavar="DIR", help="allowed directory (repeatable)")
    p.add_argument(
        "--allow-writes", action="store_true", default=None, help="enable apply_proposal"
    )
    p.add_argument("--max-runtime", metavar="DUR", help="budget ceiling, e.g. 15m")
    p.add_argument("--max-memory", metavar="SIZE", help="budget ceiling, e.g. 16GB")
    p.add_argument("--max-cpus", type=int, metavar="N", help="budget ceiling")
    p.add_argument(
        "--no-external", action="store_true", default=None, help="forbid solver binaries"
    )
    p.add_argument("--executor", choices=("process", "inline"))
    p.add_argument("--max-response-kb", type=float, metavar="KB")
    p.add_argument("--http", action="store_true", help="serve streamable HTTP on 127.0.0.1")
    p.add_argument("--port", type=int, default=0, help="HTTP port (0 = pick a free one)")
    p.add_argument("--token-file", type=Path, help="read the HTTP bearer token from this file")
    p.add_argument(
        "--service-factory",
        default=os.environ.get("OA_MCP_SERVICE_FACTORY", DEFAULT_FACTORY),
        metavar="MODULE:FUNC",
        help=f"zero-argument callable returning an OAService (default {DEFAULT_FACTORY})",
    )
    return p


def load_service(factory: str) -> OAService:
    """Import ``module:func`` and call it with no arguments (default: ``LocalService``)."""
    module_name, _, attr = factory.partition(":")
    if not module_name or not attr:
        raise SystemExit(f"oa-mcp: bad --service-factory {factory!r}; expected MODULE:FUNC")
    try:
        func: Any = getattr(importlib.import_module(module_name), attr)
    except (ImportError, AttributeError) as exc:
        raise SystemExit(f"oa-mcp: cannot load service factory {factory!r}: {exc}") from exc
    service: OAService = func()
    return service


def main(argv: Sequence[str] | None = None) -> None:
    """Entry point."""
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    args = build_parser().parse_args(argv)
    flags = {
        "root": args.root,
        "allow_writes": args.allow_writes,
        "max_runtime": args.max_runtime,
        "max_memory": args.max_memory,
        "max_cpus": args.max_cpus,
        "no_external": args.no_external,
        "executor": args.executor,
        "max_response_kb": args.max_response_kb,
    }
    settings = resolve_settings(flags, read_mcp_table())
    registry = Registry.load(load_service(args.service_factory))
    server = build_server(registry, settings)
    if args.http:
        run_http(server, load_or_create_token(args.token_file), args.port)
    else:
        run_stdio(server)
