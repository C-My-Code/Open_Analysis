# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""stdio transport hygiene: the JSON-RPC pipes live on private descriptors.

The stock MCP stdio transport reads the process's standard input and writes its standard output.
Two things go wrong when solver code runs in the same process:

* **Windows deadlock.** The transport keeps a synchronous ``ReadFile`` pending on the stdin pipe
  while it waits for the next message. Windows serializes every request on a synchronous file
  object, so any other call on that pipe blocks until a line arrives. Loading a DLL that carries
  its own C runtime (gmsh does) runs the runtime's I/O setup, which calls ``GetFileType`` on the
  standard handles; it blocks while holding the loader lock, and then every new thread blocks too.
  The client is waiting for the reply, so nothing ever arrives on stdin: ``run_analysis`` of
  ``pi.dc`` never returned.
* **Protocol pollution.** A C library, a ``print`` or a child process that writes to fd 1 would
  inject non-JSON lines into the stream, and a child that inherits stdin could eat messages.

:func:`private_stdio` therefore moves the protocol onto private (non-inheritable) duplicates of
fds 0 and 1, points fd 0 at the null device and fd 1 at stderr (also the Win32 standard handles
and ``sys.stdout``), and restores everything on exit. Nothing else in the process, nor any child
process, can reach the protocol pipes. This is transport plumbing only; no tool logic lives here.
"""

from __future__ import annotations

import io
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import BinaryIO

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.stdio import stdio_server

__all__ = ["private_stdio", "run_stdio"]

_STD_INPUT_HANDLE = -10
_STD_OUTPUT_HANDLE = -11


def _set_std_handles(fd_in: int, fd_out: int) -> None:
    """Point the Win32 standard handles at the CRT descriptors ``fd_in``/``fd_out``.

    ``GetStdHandle`` (what a DLL's C runtime and ``subprocess`` consult) reads these, not the CRT
    descriptor table, so ``dup2`` alone is not enough. No-op off Windows.
    """
    if sys.platform == "win32":
        import ctypes
        import msvcrt

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetStdHandle(_STD_INPUT_HANDLE, msvcrt.get_osfhandle(fd_in))
        kernel32.SetStdHandle(_STD_OUTPUT_HANDLE, msvcrt.get_osfhandle(fd_out))


@contextmanager
def private_stdio() -> Iterator[tuple[BinaryIO, BinaryIO]]:
    """Yield ``(protocol_in, protocol_out)`` on private copies of the original stdin/stdout.

    While inside, fd 0 reads the null device and fd 1 (and ``sys.stdout``) writes to stderr.
    Everything is restored on exit. Raises ``OSError`` when the process has no stdin/stdout.
    """
    for stream in (sys.stdout, sys.__stdout__):
        if stream is not None:
            stream.flush()
    proto_in = os.dup(0)  # os.dup is non-inheritable: child processes never get the pipes
    proto_out = os.dup(1)
    saved_stdout = sys.stdout
    null_fd = os.open(os.devnull, os.O_RDWR)
    try:
        os.dup2(null_fd, 0)
        try:
            os.dup2(2, 1)
        except OSError:  # no stderr (e.g. pythonw): discard stray output instead
            os.dup2(null_fd, 1)
    finally:
        os.close(null_fd)
    _set_std_handles(0, 1)
    sys.stdout = sys.stderr
    try:
        # closefd=False: the wrappers the transport builds may be closed or collected freely.
        with (
            open(proto_in, "rb", closefd=False) as rfile,
            open(proto_out, "wb", closefd=False) as wfile,
        ):
            yield rfile, wfile
    finally:
        sys.stdout = saved_stdout
        os.dup2(proto_in, 0)
        os.dup2(proto_out, 1)
        _set_std_handles(0, 1)
        os.close(proto_in)
        os.close(proto_out)


def run_stdio(server: FastMCP) -> None:
    """Serve ``server`` over stdio through :func:`private_stdio` (instead of ``run("stdio")``)."""
    try:
        os.fstat(0)
        os.fstat(1)
    except OSError as exc:
        raise SystemExit(f"oa-mcp: stdin/stdout are not usable for stdio: {exc}") from None
    low = server._mcp_server
    with private_stdio() as (rfile, wfile):
        # The same UTF-8 text wrapping as mcp.server.stdio.stdio_server's defaults.
        stdin = anyio.wrap_file(io.TextIOWrapper(rfile, encoding="utf-8", errors="replace"))
        stdout = anyio.wrap_file(io.TextIOWrapper(wfile, encoding="utf-8"))

        async def serve() -> None:
            async with stdio_server(stdin, stdout) as (read_stream, write_stream):
                await low.run(read_stream, write_stream, low.create_initialization_options())

        anyio.run(serve)
