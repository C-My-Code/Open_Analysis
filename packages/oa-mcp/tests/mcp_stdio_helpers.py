# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""A service whose ``open_board`` misbehaves like solver code does (WP-518 regression).

Used as ``--service-factory mcp_stdio_helpers:NoisyService`` by a real ``oa-mcp`` stdio
subprocess. Before ``open_board`` returns it:

* prints to ``sys.stdout`` and writes to fd 1 (what C libraries such as gmsh do);
* runs a small pool of child processes that inherit the standard handles, print, and read their
  stdin to the end (a child that inherited the protocol pipe would hang or eat messages);
* on Windows, queries the standard input handle (``GetFileInformationByHandle``, as the C runtime
  ``fstat`` does) from a worker thread while the transport waits for the next message. Loading
  gmsh's DLL makes the same kind of query (``pi.dc``); on the protocol pipe it is serialized behind
  the transport's pending synchronous read and blocks until the client sends another line.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from oa_service.testing import FakeService

CHILD = "import sys; print('child stdout noise', flush=True); sys.stdin.read()"


def _child(_: int) -> int:
    return subprocess.run([sys.executable, "-c", CHILD], timeout=60, check=False).returncode


def _query_std_input() -> int:
    """``GetFileInformationByHandle(GetStdHandle(STD_INPUT_HANDLE))`` (Windows; 0 elsewhere)."""
    if sys.platform != "win32":
        return 0
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetStdHandle.restype = ctypes.c_void_p
    kernel32.GetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    info = ctypes.create_string_buffer(64)  # BY_HANDLE_FILE_INFORMATION is 52 bytes
    return int(kernel32.GetFileInformationByHandle(kernel32.GetStdHandle(-10), info))


class NoisyService(FakeService):
    """``FakeService`` whose board opening writes to stdout and spawns child processes."""

    def _open(self, path: Path, read_options: Any, physics: Any, intent: Any) -> Any:
        time.sleep(0.5)  # let the transport go back to its blocking stdin read
        _query_std_input()
        print("python stdout noise", flush=True)
        os.write(1, b"fd 1 noise\n")
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(_child, range(2)))
        assert codes == [0, 0], codes
        return super()._open(path, read_options, physics, intent)
