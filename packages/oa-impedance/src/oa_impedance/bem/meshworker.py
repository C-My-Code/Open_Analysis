# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Worker process of the meshing pool (``meshpool``): ``python -m oa_impedance.bem.meshworker``.

Protocol on stdin / stdout, binary frames of an 8-byte little-endian length and a pickle. The
worker first sends ``("ready",)``, then answers every ``(job, cross_section, mesh_params)`` with
``(job, True, problem)`` or ``(job, False, message)``. EOF on stdin ends it. Both ends are this
package of the same user; nothing here is reachable from outside the process pair.
"""

from __future__ import annotations

import pickle
import struct
import sys
from typing import IO, Any

__all__ = ["main", "read_frame", "write_frame"]

_HEADER = struct.Struct("<Q")


def write_frame(out: IO[bytes], obj: Any) -> None:
    data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    out.write(_HEADER.pack(len(data)))
    out.write(data)
    out.flush()


def read_frame(inp: IO[bytes]) -> Any:
    """The next object, or ``None`` at end of stream."""
    head = inp.read(_HEADER.size)
    if len(head) < _HEADER.size:
        return None
    (size,) = _HEADER.unpack(head)
    buf = bytearray()
    while len(buf) < size:
        chunk = inp.read(size - len(buf))
        if not chunk:
            return None
        buf += chunk
    return pickle.loads(bytes(buf))


def main() -> None:
    inp, out = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr  # a stray print must not corrupt the frames
    try:
        from threadpoolctl import threadpool_limits  # type: ignore[import-untyped,unused-ignore]

        threadpool_limits(limits=1)
    except ImportError:  # pragma: no cover - optional
        pass
    from oa_impedance.bem.geometry import build_problem

    write_frame(out, ("ready",))
    while True:
        msg = read_frame(inp)
        if msg is None:
            return
        job, xs, params = msg
        try:
            write_frame(out, (job, True, build_problem(xs, params)))
        except Exception as exc:  # reported, then re-raised by the in-process path
            write_frame(out, (job, False, f"{type(exc).__name__}: {exc}"))


if __name__ == "__main__":
    main()
