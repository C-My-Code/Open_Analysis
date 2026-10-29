# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""WSL path translation, the WSL runner command line and the resource-box wrapper script."""

from __future__ import annotations

import pytest

from oa_em_palace import discovery, host_to_wsl, wsl_to_host
from oa_em_palace.paths import is_drvfs
from oa_framework.external import ExternalCommand
from oa_jobs.runners import WslRunner
from oa_jobs.tools import ToolsConfig


@pytest.mark.parametrize(
    ("host", "wsl"),
    [
        (r"D:\Documents\Projects\x y\job", "/mnt/d/Documents/Projects/x y/job"),
        ("C:/Users/km/AppData", "/mnt/c/Users/km/AppData"),
        ("e:\\", "/mnt/e"),
        (r"\\wsl$\Ubuntu\home\km\job", "/home/km/job"),
        (r"\\wsl.localhost\Ubuntu\tmp", "/tmp"),
        ("/home/km/job", "/home/km/job"),
    ],
)
def test_host_to_wsl(host: str, wsl: str) -> None:
    assert host_to_wsl(host) == wsl


@pytest.mark.parametrize("bad", ["relative\\dir", r"\\server\share\x", "D:rel"])
def test_host_to_wsl_rejects_untranslatable_paths(bad: str) -> None:
    with pytest.raises(ValueError, match="cannot translate"):
        host_to_wsl(bad)


def test_wsl_to_host_round_trip() -> None:
    assert wsl_to_host("/mnt/d/a/b") == r"D:\a\b"
    assert host_to_wsl(wsl_to_host("/mnt/c/Users/km")) == "/mnt/c/Users/km"
    assert wsl_to_host("/home/km/x", distro="Ubuntu") == r"\\wsl.localhost\Ubuntu\home\km\x"
    with pytest.raises(ValueError, match="distro"):
        wsl_to_host("/home/km/x")


def test_is_drvfs() -> None:
    assert is_drvfs("/mnt/d/job") and not is_drvfs("/home/km/job") and not is_drvfs("/mnt/wsl/x")


def test_wsl_runner_command_line_for_palace(tmp_path) -> None:  # type: ignore[no-untyped-def]
    tools = ToolsConfig(
        {
            "palace": {
                "runner": "wsl",
                "distro": "Ubuntu",
                "path": "/home/km/opt/palace/bin/oa-palace-run",
            }
        }
    )
    runner = WslRunner(tools)
    cmd = ExternalCommand(
        program="palace",
        args=("config.json",),
        workdir=tmp_path,
        env={"OA_PALACE_NP": "2", "OA_PALACE_MEM_GB": "8"},
    )
    argv = runner._argv(cmd, runner.resolve("palace"))
    assert argv[:3] == ["wsl.exe", "-d", "Ubuntu"]
    assert argv[3] == "--cd" and argv[4].startswith("/mnt/")
    tail = argv[argv.index("--") + 1 :]
    assert tail[0] == "env" and "OA_PALACE_NP=2" in tail
    assert tail[-2:] == ["/home/km/opt/palace/bin/oa-palace-run", "config.json"]


def test_wrapper_applies_the_hard_limits() -> None:
    text = discovery.wrapper_text()
    assert text.startswith("#!/usr/bin/env bash")
    assert "\r" not in text
    # the three protections the machine-safety rules demand
    assert "systemd-run --user --scope" in text
    assert "MemoryMax=${MEM_GB}G" in text and "CPUQuota=${CPU_PCT}%" in text
    assert "prlimit --as=" in text
    assert "OA_PALACE_MAX_NP:-2" in text and "nice -n 19" in text
    # MPI ranks never exceed the ceiling
    assert '[ "$NP" -gt "$MAX_NP" ]' in text
