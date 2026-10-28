# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil  # type: ignore[import-untyped]
import pytest

from oa_framework.external import CancelToken, ExternalCommand, ExternalRunner
from oa_jobs import (
    BudgetBreach,
    DockerRunner,
    LocalRunner,
    ToolsConfig,
    ToolTimeout,
    WslRunner,
    detect_wsl,
    doctor,
    host_to_wsl,
    make_runner,
    wsl_to_host,
)
from oa_jobs.procs import ProcessGuard
from oa_jobs.progress import make_parser
from oa_model.errors import Cancelled, ExternalToolFailed, ToolMissing


class Flag:
    def __init__(self) -> None:
        self.on = False

    def is_cancelled(self) -> bool:
        return self.on


def cmd(tmp_path: Path, code: str, **kw: object) -> ExternalCommand:
    return ExternalCommand(program="python", args=("-c", code), workdir=tmp_path, **kw)  # type: ignore[arg-type]


def runner(**kw: float) -> LocalRunner:
    return LocalRunner(ToolsConfig({}), grace_s=3.0, poll_s=0.02, **kw)


def test_protocol_conformance() -> None:
    assert isinstance(LocalRunner(ToolsConfig({})), ExternalRunner)
    assert isinstance(WslRunner(ToolsConfig({})), ExternalRunner)
    assert isinstance(DockerRunner(ToolsConfig({})), ExternalRunner)
    assert isinstance(Flag(), CancelToken)


@pytest.mark.parametrize(
    ("host", "wsl"),
    [
        ("D:\\x", "/mnt/d/x"),
        ("C:\\Users\\a b\\proj\\f.txt", "/mnt/c/Users/a b/proj/f.txt"),
        ("d:/mixed/slash", "/mnt/d/mixed/slash"),
        ("E:\\", "/mnt/e"),
    ],
)
def test_wsl_path_round_trip(host: str, wsl: str) -> None:
    assert host_to_wsl(host) == wsl
    back = wsl_to_host(wsl)
    assert host_to_wsl(back) == wsl
    assert back.lower().replace("/", "\\").rstrip("\\") == host.lower().replace("/", "\\").rstrip(
        "\\"
    )


def test_wsl_path_rejects_non_drive_paths() -> None:
    with pytest.raises(ValueError, match="drive letter"):
        host_to_wsl("\\\\server\\share\\x")
    with pytest.raises(ValueError, match="/mnt"):
        wsl_to_host("/home/user/x")


def test_wsl_argv_shape(tmp_path: Path) -> None:
    tools = ToolsConfig({"palace": {"runner": "wsl", "path": "/opt/palace/bin/palace"}})
    r = WslRunner(tools, distro="Ubuntu")
    info = r.resolve("palace")
    c = ExternalCommand(
        program="palace",
        args=("D:\\work\\cfg.json", "-n", "4"),
        workdir=Path("D:\\work"),
        env={"OMP_NUM_THREADS": "2"},
    )
    argv = r._argv(c, info)
    assert argv[:7] == ["wsl.exe", "-d", "Ubuntu", "--cd", "/mnt/d/work", "--", "env"]
    assert argv[7:] == [
        "OMP_NUM_THREADS=2",
        "/opt/palace/bin/palace",
        "/mnt/d/work/cfg.json",
        "-n",
        "4",
    ]


def test_docker_argv_mounts_job_dir(tmp_path: Path) -> None:
    tools = ToolsConfig({"palace": {"runner": "docker", "image": "ghcr.io/x/palace:1"}})
    r = DockerRunner(tools, max_memory_gb=2.0)
    c = ExternalCommand(program="palace", args=(str(tmp_path / "in.json"),), workdir=tmp_path)
    argv = r._argv(c, r.resolve("palace"))
    assert f"{tmp_path}:/work" in argv
    assert "--memory" in argv
    assert argv[-2:] == ["palace", "/work/in.json"]
    assert r.translate_path(tmp_path / "sub" / "f") == "/work/sub/f"


def test_detect_wsl_does_not_raise() -> None:
    info = detect_wsl(refresh=True)
    assert isinstance(info.as_dict()["available"], bool)
    if info.available:
        assert info.distros


def test_make_runner_unknown() -> None:
    with pytest.raises(ToolMissing):
        make_runner("nope")


def test_local_run_captures_logs_progress_and_outputs(tmp_path: Path) -> None:
    code = (
        "import sys\n"
        "print('start')\n"
        "for i in range(1, 5): print(f'step {i}/4', flush=True)\n"
        "print('warn', file=sys.stderr)\n"
        "open('out.txt','w').write('x')\n"
    )
    seen: list[tuple[float, str]] = []
    res = runner().run(
        cmd(tmp_path, code, progress_parser="generic", outputs=("out.txt",)),
        lambda f, m: seen.append((f, m)),
        Flag(),
    )
    assert res.returncode == 0
    assert res.runner == "local"
    assert "step 4/4" in res.stdout_tail
    assert res.stderr_tail == "warn"
    fractions = [f for f, _ in seen]
    assert fractions == sorted(fractions)
    assert 0.25 in fractions and fractions[-1] == 1.0
    assert "step 2/4" in (tmp_path / "logs" / "python.stdout.log").read_text()


def test_local_nonzero_exit_and_missing_output(tmp_path: Path) -> None:
    res = runner().run(cmd(tmp_path, "import sys; sys.exit(3)"), lambda f, m: None, Flag())
    assert res.returncode == 3
    with pytest.raises(ExternalToolFailed, match="did not produce"):
        runner().run(cmd(tmp_path, "pass", outputs=("never.txt",)), lambda f, m: None, Flag())


def test_stdin_and_env(tmp_path: Path) -> None:
    code = "import os,sys; print(sys.stdin.read().upper(), os.environ['OA_X'])"
    res = runner().run(
        cmd(tmp_path, code, stdin="abc", env={"OA_X": "1"}), lambda f, m: None, Flag()
    )
    assert res.stdout_tail == "ABC 1"


def test_cancel_kills_process_tree_quickly(tmp_path: Path) -> None:
    # parent spawns a grandchild; both sleep 60 s
    code = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "open('child.pid', 'w').write(str(p.pid))\n"
        "time.sleep(60)\n"
    )
    flag = Flag()
    pid_file = tmp_path / "child.pid"

    def cancel_when_child_started() -> None:
        deadline = time.monotonic() + 10
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        time.sleep(0.1)
        flag.on = True

    threading.Thread(target=cancel_when_child_started, daemon=True).start()
    t0 = time.monotonic()
    with pytest.raises(Cancelled):
        runner().run(cmd(tmp_path, code), lambda f, m: None, flag)
    assert time.monotonic() - t0 < 12
    child = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while psutil.pid_exists(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not psutil.pid_exists(child)  # the grandchild died with the tree


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job objects")
def test_windows_job_object_memory_limit_stops_runaway_allocation() -> None:
    guard = ProcessGuard(memory_limit_bytes=300 << 20)
    code = "import time; time.sleep(0.5); b = bytearray(b'\\x01') * (900 << 20); print('allocated')"
    proc = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    guard.assign(proc.pid)
    assert guard.job_object_active
    out, err = proc.communicate(timeout=30)
    guard.close()
    assert "allocated" not in out
    assert proc.returncode != 0 and "MemoryError" in err


def test_cancel_python_sleep_60_under_12s(tmp_path: Path) -> None:
    flag = Flag()
    threading.Timer(0.3, lambda: setattr(flag, "on", True)).start()
    t0 = time.monotonic()
    with pytest.raises(Cancelled):
        runner().run(cmd(tmp_path, "import time; time.sleep(60)"), lambda f, m: None, flag)
    assert time.monotonic() - t0 < 12


def test_timeout(tmp_path: Path) -> None:
    t0 = time.monotonic()
    with pytest.raises(ToolTimeout):
        runner().run(
            cmd(tmp_path, "import time; time.sleep(60)", timeout_s=0.3), lambda f, m: None, Flag()
        )
    assert time.monotonic() - t0 < 12


def test_memory_budget_breach_kills_process(tmp_path: Path) -> None:
    code = (
        "import time\n"
        "b = []\n"
        "for _ in range(400):\n"
        "    b.append(bytearray(b'\\x01') * (25 << 20))\n"
        "    time.sleep(0.01)\n"
    )
    t0 = time.monotonic()
    with pytest.raises(BudgetBreach) as ei:
        runner(max_memory_gb=0.25).run(cmd(tmp_path, code), lambda f, m: None, Flag())
    assert ei.value.reason == "budget:max_memory"
    assert time.monotonic() - t0 < 20


def test_missing_tool_raises_tool_missing(tmp_path: Path) -> None:
    r = LocalRunner(ToolsConfig({"ghost": {"runner": "local", "path": str(tmp_path / "nope.exe")}}))
    with pytest.raises(ToolMissing) as ei:
        r.run(
            ExternalCommand(program="ghost", args=(), workdir=tmp_path), lambda f, m: None, Flag()
        )
    assert "tools.toml" in ei.value.hint or "ghost" in ei.value.hint


def test_tools_toml_and_project_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    toml = tmp_path / "tools.toml"
    toml.write_text(
        f"[tools.mytool]\nrunner = 'local'\npath = '{Path(sys.executable).as_posix()}'\n"
        "min_version = '1.2'\nversion = '1.4.0'\n"
        "[tools.palace]\nrunner = 'wsl'\npath = '/opt/palace/bin/palace'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OA_TOOLS_TOML", str(toml))
    cfg = ToolsConfig()
    info = cfg.resolve("mytool")
    assert info.version == "1.4.0" and info.runner == "local"
    assert cfg.resolve("palace").runner == "wsl"
    assert (
        ToolsConfig(project_overrides={"mytool": {"version": "1.0"}}).entry("mytool")["version"]
        == "1.0"
    )
    with pytest.raises(ToolMissing, match="older"):
        ToolsConfig(project_overrides={"mytool": {"version": "1.0"}}).resolve("mytool")
    assert "python" in cfg.tools  # built-in


def test_doctor_report_shape() -> None:
    rep = doctor(ToolsConfig({}))
    assert rep["tools"]["python"]["ok"] is True
    assert {"wsl", "docker", "process_control", "platform"} <= set(rep)
    if sys.platform == "win32":
        assert rep["process_control"]["windows_job_objects"] is True


def test_progress_parsers() -> None:
    g = make_parser("generic")
    assert g is not None
    assert g.feed("nothing here") is None
    assert g.feed("  42.5 % done")[0] == pytest.approx(0.425)  # type: ignore[index]
    assert g.feed("step 1/10") is None  # would go backwards

    o = make_parser("openems")
    assert o is not None
    assert o.feed("Max. number of timesteps: 1000") is None
    f, msg = o.feed("[@ 4s] Timestep: 250 || Speed: 35 MC/s || Energy: ~1e-18 (-33.3dB)")  # type: ignore[misc]
    assert f == pytest.approx(0.25) and "250/1000" in msg
    o2 = make_parser("openems")
    assert o2 is not None
    f2, _ = o2.feed("Timestep: 20 || Energy: ~1e-9 (-25.0dB)")  # type: ignore[misc]
    assert f2 == pytest.approx(0.5)

    p = make_parser("palace")
    assert p is not None
    assert p.feed("Frequency 3/10 (2.0 GHz)")[0] == pytest.approx(0.3)  # type: ignore[index]
    assert make_parser(None) is None
    with pytest.raises(ValueError, match="unknown progress parser"):
        make_parser("bogus")


@pytest.mark.external
def test_wsl_runner_uname(tmp_path: Path) -> None:
    r = WslRunner(ToolsConfig({"uname": {"runner": "wsl", "path": "/usr/bin/uname"}}))
    if not r.available():
        pytest.skip("WSL not available")
    res = r.run(
        ExternalCommand(program="uname", args=("-a",), workdir=tmp_path), lambda f, m: None, Flag()
    )
    assert res.returncode == 0 and "Linux" in res.stdout_tail


@pytest.mark.external
def test_docker_runner_uname(tmp_path: Path) -> None:
    r = DockerRunner(
        ToolsConfig({"uname": {"runner": "docker", "image": "alpine:3.20", "path": "uname"}})
    )
    if not r.available() or subprocess.run(["docker", "info"], capture_output=True).returncode:
        pytest.skip("Docker not available")
    res = r.run(
        ExternalCommand(program="uname", args=("-a",), workdir=tmp_path), lambda f, m: None, Flag()
    )
    assert res.returncode == 0 and "Linux" in res.stdout_tail
