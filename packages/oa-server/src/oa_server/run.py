# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``oa serve``: run the app on 127.0.0.1 with a random bearer token."""

from __future__ import annotations

import os
import secrets
import socket
import sys
import webbrowser
from pathlib import Path

__all__ = ["default_web_dist", "serve"]


def default_web_dist() -> Path | None:
    """Packaged web/dist; ``OA_WEB_DIST`` overrides, the source tree is the fallback."""
    env = os.environ.get("OA_WEB_DIST")
    cands = [Path(env)] if env else []
    here = Path(__file__).resolve().parent
    cands += [here / "web_dist", *(p / "web" / "dist" for p in here.parents)]
    return next((c for c in cands if (c / "index.html").is_file()), None)


def serve(
    project: Path | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
) -> int:
    """Start the server (blocks). The URL with the token fragment goes to stderr."""
    import uvicorn

    from oa_server.app import create_app
    from oa_service import CallContext
    from oa_service.local import LocalService

    token = secrets.token_urlsafe(24)
    svc = LocalService()
    if project is not None:
        svc.open(project.resolve())
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: binding to {host} exposes the API beyond this machine.", file=sys.stderr)
    app = create_app(
        svc,
        token=token,
        ctx=CallContext(caller="rest", allow_writes=True),
        web_dist=default_web_dist(),
        watch=True,
    )
    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
    sock.bind((host, port))
    url = f"http://{host}:{sock.getsockname()[1]}/#token={token}"
    print(f"Open_Analysis server: {url}", file=sys.stderr, flush=True)
    if open_browser:
        webbrowser.open(url)
    uvicorn.Server(uvicorn.Config(app, log_level="warning")).run(sockets=[sock])
    return 0
