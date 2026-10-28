# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared fixtures: golden paths, a local ``ExternalRunner`` shim and an inline analysis.

``oa_jobs`` (WP-107) is built in parallel, so end-to-end tests run the generated scripts through
``LocalRunner`` below, a minimal ``ExternalRunner`` (subprocess, no cancellation), driven by
``oa_testkit.harness.run_inline_full`` through :class:`OpenEMSInlineAnalysis`.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, ClassVar

import pytest

from oa_em_openems import OpenEMSAdapter, discover
from oa_em_openems.discovery import OpenEMSInstall
from oa_framework.analysis import Plan, Task, TaskOutput
from oa_framework.external import ExternalCommand, ExternalResult, ToolInfo
from oa_model._base import OAModel
from oa_model.common import Diagnostic
from oa_model.em import EMProblem
from oa_results.bundle import Provenance, ResultBundle, ResultItem

REPO = Path(__file__).resolve().parents[3]
GOLDEN = REPO / "tests" / "golden" / "em_openems"


class LocalRunner:
    """Minimal local ``ExternalRunner``: ``[tool.path, *args]`` in ``cmd.workdir``."""

    name: ClassVar[str] = "local"

    def __init__(self, install: OpenEMSInstall) -> None:
        self.install = install
        self.peak_rss_mb: float = 0.0

    def available(self) -> bool:
        return True

    def resolve(self, program: str) -> ToolInfo:
        return self.install.tool_info()

    def translate_path(self, host_path: Path) -> str:
        return str(host_path)

    def run(
        self,
        cmd: ExternalCommand,
        on_progress: Callable[[float, str], None],
        cancel: Any,
    ) -> ExternalResult:
        tool = self.resolve(cmd.program)
        env = {**os.environ, **cmd.env}
        t0 = time.perf_counter()
        lines: list[str] = []
        peak = 0.0
        with subprocess.Popen(
            [str(tool.path), "-I", *cmd.args],
            cwd=cmd.workdir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        ) as proc:
            assert proc.stdout is not None
            sampler = _RssSampler(proc.pid)
            for line in proc.stdout:
                lines.append(line.rstrip("\n"))
                peak = max(peak, sampler.sample())
                if "Timestep:" in line:
                    on_progress(0.0, line.strip())
            proc.wait()
            sampler.close()
        self.peak_rss_mb = max(self.peak_rss_mb, peak)
        text = "\n".join(lines)
        (cmd.workdir / "run.log").write_text(text, encoding="utf-8")
        tail = "\n".join(lines[-200:])
        return ExternalResult(
            returncode=proc.returncode,
            stdout_tail=tail,
            stderr_tail="",
            wall_s=time.perf_counter() - t0,
            runner=self.name,
            tool_version=tool.version,
        )


class _RssSampler:
    """Peak working set [MB] of a child process tree on Windows (ctypes) or Linux (/proc).

    On Windows the venv ``python.exe`` is a launcher that spawns the real interpreter, so the
    whole tree below ``pid`` is measured.
    """

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._peak: dict[int, float] = {}

    def close(self) -> None:
        return None

    def _children(self) -> list[int]:
        import ctypes
        from ctypes import wintypes

        class Entry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_char * 260),
            ]

        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        snap = k32.CreateToolhelp32Snapshot(2, 0)
        parents: dict[int, int] = {}
        e = Entry()
        e.dwSize = ctypes.sizeof(e)
        ok = k32.Process32First(ctypes.c_void_p(snap), ctypes.byref(e))
        while ok:
            parents[e.th32ProcessID] = e.th32ParentProcessID
            ok = k32.Process32Next(ctypes.c_void_p(snap), ctypes.byref(e))
        k32.CloseHandle(ctypes.c_void_p(snap))
        tree = {self.pid}
        changed = True
        while changed:
            changed = False
            for child, parent in parents.items():
                if parent in tree and child not in tree:
                    tree.add(child)
                    changed = True
        return sorted(tree)

    def _win_peak(self, pid: int) -> float:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        psapi = ctypes.windll.psapi  # type: ignore[attr-defined]
        k32.OpenProcess.restype = ctypes.c_void_p
        handle = k32.OpenProcess(0x0410, False, pid)
        if not handle:
            return 0.0
        c = Counters()
        c.cb = ctypes.sizeof(c)
        psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(Counters),
            wintypes.DWORD,
        ]
        got = psapi.GetProcessMemoryInfo(ctypes.c_void_p(handle), ctypes.byref(c), c.cb)
        k32.CloseHandle(ctypes.c_void_p(handle))
        return float(c.PeakWorkingSetSize) / 1e6 if got else 0.0

    def sample(self) -> float:
        if os.name == "nt":
            for pid in self._children():
                self._peak[pid] = max(self._peak.get(pid, 0.0), self._win_peak(pid))
            return max(self._peak.values(), default=0.0)
        try:
            for line in Path(f"/proc/{self.pid}/status").read_text().splitlines():
                if line.startswith("VmHWM:"):
                    return float(line.split()[1]) / 1e3
        except OSError:
            return 0.0
        return 0.0


class _Cfg(OAModel):
    pass


class _Payload(OAModel):
    index: int


class OpenEMSInlineAnalysis:
    """Test analysis: ``prepare`` in ``plan``, a task per excitation, ``collect`` in ``reduce``."""

    kind: ClassVar[str] = "test.openems"
    version: ClassVar[str] = "1"
    title: ClassVar[str] = "openEMS inline test"
    Config: ClassVar[type[OAModel]] = _Cfg
    TaskPayload: ClassVar[type[OAModel]] = _Payload

    def __init__(self, adapter: OpenEMSAdapter, problem: EMProblem, workdir: Path, tool: ToolInfo):
        self.adapter, self.problem, self.workdir, self.tool = adapter, problem, workdir, tool
        self.prepared = adapter.prepare(problem, workdir, tool)
        self.results: dict[int, ExternalResult] = {}
        self.output = None

    def validate_setup(self, ctx: Any, cfg: Any) -> tuple[Diagnostic, ...]:
        return self.adapter.check(self.problem)

    def plan(self, ctx: Any, cfg: Any) -> Plan:
        return Plan(
            tasks=tuple(
                Task(id=f"exc:{i}", analysis=self.kind, payload={"index": i})
                for i in range(len(self.prepared.commands))
            )
        )

    def run_task(self, ctx: Any, payload: Any) -> TaskOutput:
        res = ctx.run_external(self.prepared.commands[payload.index])
        self.results[payload.index] = res
        return TaskOutput(
            task_id=ctx.task.id,
            data={"returncode": res.returncode, "wall_s": res.wall_s},
        )

    def reduce(self, ctx: Any, cfg: Any, outputs: Mapping[str, TaskOutput]) -> ResultBundle:
        ordered = [self.results[i] for i in sorted(self.results)]
        self.output = self.adapter.collect(self.problem, self.prepared, ordered)
        net = self.output.network
        items = (
            (ResultItem(id="sparams", title="S-parameters", kind="network", data=net),)
            if net is not None
            else ()
        )
        return ResultBundle(
            id="01OPENEMSTEST",
            status="ok" if net is not None else "error",
            summary="openEMS test run",
            items=items,
            provenance=Provenance(
                analysis=self.kind,
                analysis_version=self.version,
                board_hash=ctx.board().content_hash(),
                physics_hash=None,
                setup_hash="sha256:test",
                config={},
                solver="openems",
                solver_version=self.output.solver_version,
                started_utc="2026-01-01T00:00:00Z",
                finished_utc="2026-01-01T00:00:00Z",
            ),
        )


@pytest.fixture(scope="session")
def install() -> OpenEMSInstall:
    found = discover(cwd=REPO)
    if found is None:
        pytest.skip("openEMS >= 0.37 not found (see packages/oa-em-openems/README.md)")
    return found


@pytest.fixture(scope="session")
def local_runner(install: OpenEMSInstall) -> LocalRunner:
    return LocalRunner(install)


@pytest.fixture(scope="session")
def inline_analysis_cls() -> type[OpenEMSInlineAnalysis]:
    return OpenEMSInlineAnalysis
