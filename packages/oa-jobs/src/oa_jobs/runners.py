# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""External runners: ``local``, ``wsl`` and ``docker`` (interfaces.md section 4.3).

GPL boundary: solvers run as separate processes only; nothing here imports or links them.

All three share one execution loop (:class:`_ProcessRunner`): stdout/stderr are streamed line by
line into ``<workdir>/logs/<program>.{stdout,stderr}.log``, each line is offered to the progress
parser, the last 200 lines of each stream are returned, and the loop polls for cancellation,
timeout and (local only) the memory budget.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import ClassVar, TextIO

from oa_framework.external import (
    CancelToken,
    ExternalCommand,
    ExternalResult,
    ToolInfo,
)
from oa_jobs.models import BudgetBreach
from oa_jobs.procs import ProcessGuard, popen_kwargs, tree_rss_bytes
from oa_jobs.progress import make_parser
from oa_jobs.tools import ToolsConfig
from oa_model.errors import Cancelled, ExternalToolFailed, OAError, ToolMissing

__all__ = [
    "DockerRunner",
    "LocalRunner",
    "ToolTimeout",
    "WslInfo",
    "WslRunner",
    "detect_wsl",
    "host_to_wsl",
    "make_runner",
    "wsl_to_host",
]

TAIL_LINES = 200
_WIN_ABS = re.compile(r"^[A-Za-z]:[\\/]")


class ToolTimeout(OAError):
    code = "TIMEOUT"


# -- WSL path translation and detection -----------------------------------------------------


def host_to_wsl(path: str | os.PathLike[str]) -> str:
    r"""``D:\x\y`` -> ``/mnt/d/x/y``. Raises ``ValueError`` for paths without a drive letter."""
    p = PureWindowsPath(os.fspath(path))
    drive = p.drive
    if len(drive) != 2 or drive[1] != ":":
        raise ValueError(f"cannot translate {str(path)!r} to a WSL path (needs a drive letter)")
    rest = "/".join(p.parts[1:])
    return f"/mnt/{drive[0].lower()}" + (f"/{rest}" if rest else "")


def wsl_to_host(path: str) -> str:
    r"""``/mnt/d/x/y`` -> ``D:\x\y``. Raises ``ValueError`` outside ``/mnt/<drive>``."""
    parts = PurePosixPath(path).parts
    if len(parts) < 3 or parts[0] != "/" or parts[1] != "mnt" or len(parts[2]) != 1:
        raise ValueError(f"{path!r} is not under /mnt/<drive>")
    return str(PureWindowsPath(f"{parts[2].upper()}:\\", *parts[3:]))


class WslInfo:
    def __init__(
        self,
        available: bool,
        distros: tuple[str, ...] = (),
        default: str | None = None,
        inside_wsl: bool = False,
        note: str = "",
    ) -> None:
        self.available = available
        self.distros = distros
        self.default = default
        self.inside_wsl = inside_wsl
        self.note = note

    def as_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "distros": list(self.distros),
            "default": self.default,
            "inside_wsl": self.inside_wsl,
            "note": self.note,
        }


_wsl_cache: WslInfo | None = None


def detect_wsl(refresh: bool = False) -> WslInfo:
    """Detect WSL. On Windows lists distros with ``wsl.exe -l -q``; on Linux checks for being
    inside WSL (``/proc/version``). The result is cached."""
    global _wsl_cache
    if _wsl_cache is not None and not refresh:
        return _wsl_cache
    info: WslInfo
    if sys.platform == "win32":
        exe = shutil.which("wsl.exe")
        if exe is None:
            info = WslInfo(False, note="wsl.exe not found")
        else:
            try:
                out = subprocess.run(
                    [exe, "-l", "-q"],
                    capture_output=True,
                    timeout=15,
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                raw = out.stdout
                text = (
                    raw.decode("utf-16-le", errors="ignore")
                    if b"\x00" in raw
                    else raw.decode("utf-8", errors="ignore")
                )
                distros = tuple(
                    ln.strip().replace("\x00", "") for ln in text.splitlines() if ln.strip()
                )
                info = WslInfo(
                    bool(distros),
                    distros,
                    distros[0] if distros else None,
                    note="" if distros else "wsl.exe present but no distribution installed",
                )
            except (OSError, subprocess.SubprocessError) as exc:
                info = WslInfo(False, note=f"wsl.exe failed: {exc}")
    else:
        inside = False
        with contextlib.suppress(OSError):
            inside = "microsoft" in Path("/proc/version").read_text(errors="ignore").lower()
        info = WslInfo(
            False, inside_wsl=inside, note="running inside WSL" if inside else "not Windows"
        )
    _wsl_cache = info
    return info


# -- shared execution loop ------------------------------------------------------------------


class _ProcessRunner:
    name: ClassVar[str] = "process"

    def __init__(
        self,
        tools: ToolsConfig | None = None,
        *,
        max_memory_gb: float | None = None,
        grace_s: float = 10.0,
        poll_s: float = 0.05,
    ) -> None:
        self.tools = tools or ToolsConfig()
        self.max_memory_gb = max_memory_gb
        self.grace_s = grace_s
        self.poll_s = poll_s

    # subclasses
    def _argv(self, cmd: ExternalCommand, info: ToolInfo) -> list[str]:
        raise NotImplementedError

    def _on_kill(self) -> None:  # extra cleanup hook (docker kill)
        return

    def resolve(self, program: str) -> ToolInfo:
        return self.tools.resolve(program)

    def _tool_version(self, info: ToolInfo) -> str | None:
        return info.version

    def run(
        self,
        cmd: ExternalCommand,
        on_progress: Callable[[float, str], None],
        cancel: CancelToken,
    ) -> ExternalResult:
        info = self.resolve(cmd.program)
        argv = self._argv(cmd, info)
        logs = cmd.workdir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, **cmd.env}
        limit = self.max_memory_gb
        job_limit = int(max(2 * limit, limit + 0.5) * 1e9) if limit else None  # backstop, see procs
        guard = ProcessGuard(job_limit)
        parser = make_parser(cmd.progress_parser)
        t0 = time.monotonic()
        try:
            proc = subprocess.Popen(
                argv,
                cwd=cmd.workdir,
                env=env,
                stdin=subprocess.PIPE if cmd.stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **popen_kwargs(),
            )
        except OSError as exc:
            guard.close()
            raise ToolMissing(
                f"Cannot start {argv[0]!r}: {exc}",
                program=cmd.program,
                hint="Check the path in tools.toml.",
            ) from exc
        guard.assign(proc.pid)
        tails: dict[str, deque[str]] = {
            "out": deque(maxlen=TAIL_LINES),
            "err": deque(maxlen=TAIL_LINES),
        }
        files: list[TextIO] = []

        def pump(stream: TextIO, tag: str, log_name: str) -> None:
            fh = open(logs / log_name, "w", encoding="utf-8", newline="\n")  # noqa: SIM115
            files.append(fh)
            for line in stream:
                fh.write(line)
                fh.flush()
                tails[tag].append(line.rstrip("\n"))
                if parser is not None:
                    try:
                        hit = parser.feed(line)
                    except Exception:  # a broken parser must not kill the run
                        hit = None
                    if hit is not None:
                        on_progress(*hit)

        assert proc.stdout is not None and proc.stderr is not None
        stem = Path(cmd.program).name
        threads = [
            threading.Thread(
                target=pump, args=(proc.stdout, "out", f"{stem}.stdout.log"), daemon=True
            ),
            threading.Thread(
                target=pump, args=(proc.stderr, "err", f"{stem}.stderr.log"), daemon=True
            ),
        ]
        for t in threads:
            t.start()
        if cmd.stdin is not None and proc.stdin is not None:
            try:
                proc.stdin.write(cmd.stdin)
                proc.stdin.close()
            except OSError:
                pass

        outcome: BaseException | None = None
        try:
            while proc.poll() is None:
                if cancel.is_cancelled():
                    outcome = Cancelled(f"{cmd.program} cancelled")
                elif cmd.timeout_s is not None and time.monotonic() - t0 > cmd.timeout_s:
                    outcome = ToolTimeout(f"{cmd.program} exceeded timeout of {cmd.timeout_s:g} s")
                elif limit is not None and tree_rss_bytes(proc.pid) > limit * 1e9:
                    outcome = BudgetBreach(
                        "budget:max_memory", f"{cmd.program} exceeded {limit:g} GB of memory"
                    )
                if outcome is not None:
                    self._stop(proc, guard)
                    break
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(timeout=self.poll_s)
        finally:
            if proc.poll() is None:
                self._stop(proc, guard)
            for t in threads:
                t.join(timeout=5)
            for fh in files:
                fh.close()
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                if pipe is not None:
                    with contextlib.suppress(OSError):
                        pipe.close()
            guard.close()
        if outcome is not None:
            raise outcome
        wall = time.monotonic() - t0
        if proc.returncode == 0:
            missing = [o for o in cmd.outputs if not (cmd.workdir / o).exists()]
            if missing:
                raise ExternalToolFailed(
                    f"{cmd.program} finished but did not produce {missing}",
                    returncode=0,
                    log_tail="\n".join(tails["err"]),
                )
            on_progress(1.0, "finished")
        return ExternalResult(
            returncode=int(proc.returncode),
            stdout_tail="\n".join(tails["out"]),
            stderr_tail="\n".join(tails["err"]),
            wall_s=wall,
            runner=self.name,
            tool_version=self._tool_version(info),
        )

    def _stop(self, proc: subprocess.Popen[str], guard: ProcessGuard) -> None:
        """Terminate, wait up to ``grace_s``, then kill the whole tree."""
        guard.terminate()
        self._on_kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=self.grace_s)
        guard.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)


class LocalRunner(_ProcessRunner):
    name: ClassVar[str] = "local"

    def available(self) -> bool:
        return True

    def _argv(self, cmd: ExternalCommand, info: ToolInfo) -> list[str]:
        assert info.path is not None
        return [info.path, *cmd.args]

    def translate_path(self, host_path: Path) -> str:
        return str(host_path)


class WslRunner(_ProcessRunner):
    r"""Runs ``wsl.exe -d <distro> --cd <wslpath> -- env K=V <program> args``.

    Windows only. Host paths in ``args`` (``D:\x``) are translated to ``/mnt/d/x``. The memory
    budget is *not* enforced here (the Linux-side process is outside the host job object);
    the job-level watchdog still samples ``wsl.exe``. Killing ``wsl.exe`` ends the session.
    """

    name: ClassVar[str] = "wsl"

    def __init__(
        self,
        tools: ToolsConfig | None = None,
        *,
        distro: str | None = None,
        max_memory_gb: float | None = None,
        grace_s: float = 10.0,
        poll_s: float = 0.05,
    ) -> None:
        super().__init__(tools, max_memory_gb=max_memory_gb, grace_s=grace_s, poll_s=poll_s)
        self.distro = distro

    def available(self) -> bool:
        return sys.platform == "win32" and detect_wsl().available

    def resolve(self, program: str) -> ToolInfo:
        info = self.tools.resolve(program, probe_version=False)
        if info.path is None:
            raise ToolMissing(
                f"Tool {program!r} has no path inside WSL",
                program=program,
                hint=f"Set path = '/usr/bin/{program}' in [tools.{program}].",
            )
        return info

    def translate_path(self, host_path: Path) -> str:
        return host_to_wsl(host_path)

    def _arg(self, a: str) -> str:
        return host_to_wsl(a).replace("\\", "/") if _WIN_ABS.match(a) else a

    def _argv(self, cmd: ExternalCommand, info: ToolInfo) -> list[str]:
        distro = self.distro or self.tools.entry(cmd.program).get("distro")
        argv = ["wsl.exe"]
        if distro:
            argv += ["-d", distro]
        argv += ["--cd", self.translate_path(cmd.workdir), "--"]
        if cmd.env:
            argv += ["env", *(f"{k}={v}" for k, v in cmd.env.items())]
        assert info.path is not None
        return [*argv, info.path, *(self._arg(a) for a in cmd.args)]


class DockerRunner(_ProcessRunner):
    """``docker run --rm -v <workdir>:/work -w /work [--user uid:gid] [--memory] <image> ...``."""

    name: ClassVar[str] = "docker"

    def __init__(
        self,
        tools: ToolsConfig | None = None,
        *,
        max_memory_gb: float | None = None,
        grace_s: float = 10.0,
        poll_s: float = 0.05,
    ) -> None:
        super().__init__(tools, max_memory_gb=max_memory_gb, grace_s=grace_s, poll_s=poll_s)
        self.bind_root: Path | None = None
        self._container: str | None = None

    def available(self) -> bool:
        return shutil.which("docker") is not None

    def resolve(self, program: str) -> ToolInfo:
        return self.tools.resolve(program, probe_version=False)

    def translate_path(self, host_path: Path) -> str:
        if self.bind_root is None:
            raise ValueError("no job directory is mounted yet (translate_path is valid in run())")
        rel = Path(host_path).resolve().relative_to(self.bind_root.resolve())
        return "/work" + ("/" + rel.as_posix() if rel.as_posix() != "." else "")

    def _arg(self, a: str) -> str:
        try:
            if os.path.isabs(a) and self.bind_root is not None:
                return self.translate_path(Path(a))
        except ValueError:
            pass
        return a

    def _argv(self, cmd: ExternalCommand, info: ToolInfo) -> list[str]:
        assert info.image is not None
        self.bind_root = cmd.workdir
        self._container = f"oa-{os.getpid()}-{int(time.monotonic() * 1e6)}"
        argv = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--name",
            self._container,
            "-v",
            f"{cmd.workdir}:/work",
            "-w",
            "/work",
        ]
        if sys.platform != "win32" and hasattr(os, "getuid"):
            argv += ["--user", f"{os.getuid()}:{os.getgid()}"]
        if self.max_memory_gb:
            argv += ["--memory", f"{self.max_memory_gb:g}g"]
        for k, v in cmd.env.items():
            argv += ["-e", f"{k}={v}"]
        prog = self.tools.entry(cmd.program).get("path", cmd.program)
        return [*argv, info.image, prog, *(self._arg(a) for a in cmd.args)]

    def _on_kill(self) -> None:
        if self._container:
            subprocess.run(
                ["docker", "kill", self._container], capture_output=True, timeout=15, check=False
            )


_RUNNERS: dict[str, type[_ProcessRunner]] = {
    "local": LocalRunner,
    "wsl": WslRunner,
    "docker": DockerRunner,
}


def make_runner(
    name: str,
    tools: ToolsConfig | None = None,
    *,
    max_memory_gb: float | None = None,
    grace_s: float = 10.0,
    poll_s: float = 0.05,
) -> _ProcessRunner:
    try:
        cls = _RUNNERS[name]
    except KeyError:
        raise ToolMissing(
            f"Unknown runner {name!r}", hint=f"Known runners: {', '.join(sorted(_RUNNERS))}."
        ) from None
    return cls(tools, max_memory_gb=max_memory_gb, grace_s=grace_s, poll_s=poll_s)
