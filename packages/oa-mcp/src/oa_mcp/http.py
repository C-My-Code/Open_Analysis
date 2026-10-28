# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Streamable-HTTP transport: loopback only, bearer token, Origin check (section 8.1)."""

from __future__ import annotations

import secrets
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

import anyio
import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def origin_is_loopback(origin: str) -> bool:
    """True when an ``Origin`` header names a loopback host (blocks DNS rebinding)."""
    parts = urlsplit(origin)
    return parts.scheme in ("http", "https") and (parts.hostname or "") in LOOPBACK_HOSTS


class GuardMiddleware:
    """Pure ASGI middleware: reject foreign ``Origin`` headers and missing/wrong bearer tokens."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        origin = headers.get("origin")
        if origin is not None and not origin_is_loopback(origin):
            await JSONResponse({"error": "origin not allowed"}, status_code=403)(
                scope, receive, send
            )
            return
        auth = headers.get("authorization", "")
        supplied = auth[7:].encode() if auth.lower().startswith("bearer ") else b""
        if not secrets.compare_digest(supplied, self._token):
            await JSONResponse(
                {"error": "missing or invalid bearer token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)


def make_http_app(server: FastMCP, token: str) -> ASGIApp:
    """The MCP endpoint (``/mcp``) wrapped in the guard."""
    return GuardMiddleware(server.streamable_http_app(), token)


def load_or_create_token(token_file: Path | None) -> str:
    """Read the token from ``token_file`` or generate a random one."""
    if token_file is not None:
        token = token_file.read_text(encoding="utf-8").strip()
        if len(token) < 16:
            raise ValueError("token file must contain at least 16 characters")
        return token
    return secrets.token_urlsafe(32)


def bind_loopback(port: int) -> socket.socket:
    """Bind a listening socket on 127.0.0.1 (``port=0`` picks a free one)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", port))
    sock.listen(16)
    return sock


def make_uvicorn(server: FastMCP, token: str) -> uvicorn.Server:
    """A uvicorn server for the guarded app. Set ``should_exit`` to stop it gracefully."""
    config = uvicorn.Config(make_http_app(server, token), log_level="warning", lifespan="on")
    return uvicorn.Server(config)


async def serve_http(server: FastMCP, token: str, sock: socket.socket) -> None:
    """Serve until stopped. The URL and token are printed to stderr (never stdout)."""
    port = sock.getsockname()[1]
    print(f"oa-mcp: listening on http://127.0.0.1:{port}/mcp", file=sys.stderr, flush=True)
    print(f"oa-mcp: bearer token: {token}", file=sys.stderr, flush=True)
    await make_uvicorn(server, token).serve(sockets=[sock])


def run_http(server: FastMCP, token: str, port: int) -> None:
    """Blocking entry point used by the CLI."""
    sock = bind_loopback(port)
    anyio.run(serve_http, server, token, sock)
