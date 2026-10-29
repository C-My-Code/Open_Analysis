# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Worker process of the meshing pool (``python -I -m oa_mesh._worker``).

Reads pickled ``(fn_name, args)`` tasks from stdin and writes pickled ``("ok", result)``,
``("mesh_error", dict)`` or ``("exc", traceback)`` replies on the original stdout; ``None`` ends the
loop. Anything gmsh or a library prints is redirected to stderr so it cannot corrupt the protocol.
"""

from __future__ import annotations

import os
import pickle
import sys
import traceback


def main() -> None:
    inp = sys.stdin.buffer
    out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)  # C-level prints (gmsh) go to stderr
    sys.stdout = sys.stderr
    from oa_mesh import sheets
    from oa_mesh.errors import MeshError

    table = {"sheet": sheets.mesh_prepared}
    while True:
        try:
            task = pickle.load(inp)
        except EOFError:
            return
        if task is None:
            return
        name, args = task
        try:
            reply: tuple[str, object] = ("ok", table[name](*args))
        except MeshError as exc:
            reply = (
                "mesh_error",
                {
                    "message": exc.message,
                    "code": exc.code,
                    "path": exc.path,
                    "hint": exc.hint,
                    "details": exc.details,
                },
            )
        except BaseException:
            reply = ("exc", traceback.format_exc())
        pickle.dump(reply, out, protocol=pickle.HIGHEST_PROTOCOL)
        out.flush()


if __name__ == "__main__":
    main()
