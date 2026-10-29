# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Host <-> WSL path translation for Palace runs.

The job directory lives on the Windows side; Palace runs in WSL2. ``config.json`` only holds
paths relative to the job directory (the runner starts Palace there), so translation is needed for
the working directory, tool paths and anything a caller passes in ``env``. Three forms are
understood:

* ``D:\\x\\y`` and ``D:/x/y`` <-> ``/mnt/d/x/y`` (drvfs mounts of Windows drives);
* ``\\\\wsl$\\Ubuntu\\home\\u`` and ``\\\\wsl.localhost\\Ubuntu\\home\\u`` <-> ``/home/u`` (files on
  the Linux file system, seen from Windows).

Working on ``/mnt/<drive>`` goes through the 9p/drvfs bridge, which is slow for the many small
reads of a Palace start. ``oa-palace-run`` stages the job to the Linux file system when it detects
a ``/mnt`` working directory (see ``scripts/oa-palace-run``).
"""

from __future__ import annotations

import os
import re
from pathlib import PurePosixPath, PureWindowsPath

__all__ = ["host_to_wsl", "is_drvfs", "wsl_to_host"]

_UNC = re.compile(
    r"^[\\/]{2}(?:wsl\$|wsl\.localhost)[\\/]+([^\\/]+)(?:[\\/]+(.*))?$", re.IGNORECASE
)


def host_to_wsl(path: str | os.PathLike[str]) -> str:
    r"""Translate a Windows path to the path Palace sees inside WSL.

    ``D:\x\y`` -> ``/mnt/d/x/y``; ``\\wsl$\Ubuntu\home\u\j`` -> ``/home/u/j``. POSIX paths pass
    through unchanged. Raises ``ValueError`` for a relative Windows path or a UNC share that is not
    a WSL distro (it has no Linux-side name).
    """
    text = os.fspath(path)
    m = _UNC.match(text)
    if m is not None:
        rest = (m.group(2) or "").replace("\\", "/")
        return "/" + rest if rest else "/"
    if text.startswith(("\\\\", "//")):
        raise ValueError(f"cannot translate network path {text!r} to a WSL path")
    p = PureWindowsPath(text)
    if p.drive:
        if len(p.drive) != 2 or p.drive[1] != ":" or not p.is_absolute():
            raise ValueError(f"cannot translate {text!r} to a WSL path (needs an absolute path)")
        rest = "/".join(p.parts[1:])
        return f"/mnt/{p.drive[0].lower()}" + (f"/{rest}" if rest else "")
    if text.startswith("/"):
        return text
    raise ValueError(f"cannot translate relative path {text!r} to a WSL path")


def wsl_to_host(path: str, *, distro: str | None = None) -> str:
    r"""Inverse of :func:`host_to_wsl`.

    ``/mnt/d/x/y`` -> ``D:\x\y``. A path on the Linux file system needs the distro name and becomes
    ``\\wsl.localhost\<distro>\...``; without ``distro`` it raises ``ValueError``.
    """
    parts = PurePosixPath(path).parts
    if not parts or parts[0] != "/":
        raise ValueError(f"{path!r} is not an absolute POSIX path")
    if len(parts) >= 3 and parts[1] == "mnt" and len(parts[2]) == 1 and parts[2].isalpha():
        return str(PureWindowsPath(f"{parts[2].upper()}:\\", *parts[3:]))
    if distro is None:
        raise ValueError(f"{path!r} is not under /mnt/<drive>; pass distro= for a Linux path")
    return "\\\\wsl.localhost\\" + "\\".join((distro, *parts[1:]))


def is_drvfs(path: str) -> bool:
    """True for a WSL path on a mounted Windows drive (``/mnt/<drive>/...``, slow 9p access)."""
    parts = PurePosixPath(path).parts
    return len(parts) >= 3 and parts[1] == "mnt" and len(parts[2]) == 1 and parts[2].isalpha()
