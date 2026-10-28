# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The `oa` CLI as a subprocess: envelopes, exit codes, determinism, non-interactivity."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from oa_service import Registry, mask_volatile

REPO = Path(__file__).resolve().parents[3]
SERVICE_TESTS = REPO / "packages" / "oa-service" / "tests"
BOARDS = REPO / "tests" / "boards" / "kicad10"
SCHEMAS = REPO / "schemas"


class Cli:
    def __init__(self, tmp: Path) -> None:
        self.env = {
            **os.environ,
            "OA_CACHE_DIR": str(tmp / "cache"),
            "OA_TOOLS_TOML": str(tmp / "tools.toml"),
            "OA_REGISTRY_FACTORY": "service_helpers:make_registry",
            "PYTHONPATH": str(SERVICE_TESTS),
            "PYTHONIOENCODING": "utf-8",
        }
        self.env.pop("OA_OUTPUT", None)

    def run(self, *args: str, timeout: float = 120.0) -> tuple[int, str, str]:
        cp = subprocess.run(
            [sys.executable, "-m", "oa_cli.main", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=self.env,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
        return cp.returncode, cp.stdout, cp.stderr

    def json(self, *args: str) -> tuple[int, dict[str, Any]]:
        rc, out, err = self.run(*args, "--json")
        lines = out.splitlines()
        assert len(lines) == 1, f"stdout must be exactly one envelope line; got {out!r} / {err!r}"
        doc = json.loads(lines[0])
        assert list(doc) == ["schema", "version", "ok", "data", "warnings", "errors"]
        assert doc["ok"] is (rc < 2), (rc, doc["errors"])
        return rc, doc


@pytest.fixture(scope="module")
def cli(tmp_path_factory: pytest.TempPathFactory) -> Cli:
    return Cli(tmp_path_factory.mktemp("cli"))


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("proj") / "combined"
    shutil.copytree(BOARDS / "combined", d)
    (d / "oa-project.yaml").write_text(
        "schema: oa-project/1\nname: combined\nboard: board.kicad_pcb\n", encoding="utf-8"
    )
    return d


def test_erc_exit_codes_fail_on(cli: Cli, project: Path) -> None:
    rc, doc = cli.json("erc", str(project), "--fail-on", "error", "--executor", "inline")
    assert rc == 1 and doc["ok"] is True and doc["data"]["job"]["state"] == "succeeded"
    clean = BOARDS / "microstrip" / "board.kicad_pcb"
    rc, doc = cli.json("erc", str(clean), "--fail-on", "error", "--executor", "inline")
    assert rc == 0
    rc, doc = cli.json("erc", str(project), "--fail-on", "never", "--executor", "inline")
    assert rc == 0


def test_documented_exit_codes(cli: Cli, project: Path) -> None:
    # 2: bad setup (misspelled field) with a JSON-Pointer path
    rc, doc = cli.json("run", str(project), "--analysis", "dummy.work", "--config", '{"n_taks": 3}')
    e = doc["errors"][0]
    assert rc == 2 and e["code"] == "SETUP_INVALID" and e["path"] == "/config/n_taks" and e["hint"]
    # 3: missing tool / analysis
    rc, doc = cli.json(
        "impedance",
        "xs",
        "--quick",
        '{"topology":"microstrip","w":"5mil","h":"4mil","t":"1mil","er":4}',
    )
    assert rc == 3 and doc["errors"][0]["code"] == "ANALYSIS_UNAVAILABLE"
    # 5: failing quality gate
    rc, doc = cli.json(
        "run", str(project), "--analysis", "gate.stub", "--config", "{}", "--executor", "inline"
    )
    assert rc == 5 and doc["errors"][0]["code"] == "QUALITY_GATE_FAILED"
    # 6: budget breach
    rc, doc = cli.json(
        "run", str(project), "--analysis", "dummy.work", "--config",
        '{"n_tasks": 2, "est_seconds": 900}', "--max-runtime", "5", "--executor", "inline",
    )  # fmt: skip
    assert rc == 6 and doc["errors"][0]["code"] == "BUDGET_EXCEEDED"
    assert doc["errors"][0]["details"]["estimate"]["est_seconds"] == 1800.0
    # 6: --timeout reached
    rc, doc = cli.json(
        "run", str(project), "--analysis", "dummy.work", "--config",
        '{"n_tasks": 1, "sleep_s": 20}', "--timeout", "0.5", "--executor", "inline",
    )  # fmt: skip
    assert rc == 6 and doc["errors"][0]["code"] == "TIMEOUT"
    # 7: a write without --yes; 0 with it
    args = (
        "setups",
        "put",
        "demo",
        "--project",
        str(project),
        "--analysis",
        "erc",
        "--config",
        "{}",
    )
    rc, doc = cli.json(*args)
    assert rc == 7 and doc["errors"][0]["code"] == "CONFIRMATION_REQUIRED"
    assert not (project / "oa" / "setups" / "demo.yaml").exists()
    rc, doc = cli.json(*args, "--yes")
    assert rc == 0 and (project / "oa" / "setups" / "demo.yaml").is_file()
    # 2: usage errors are envelopes too
    rc, out, _ = cli.run("board", "nets", "--limit", "x", "--project", str(project), "--json")
    assert rc == 2 and json.loads(out)["errors"][0]["code"] in ("SETUP_INVALID", "USAGE")
    rc, out, _ = cli.run("nonsense", "--json")
    assert rc == 2 and json.loads(out)["errors"][0]["code"] == "USAGE"


def test_no_blocking_on_closed_stdin(cli: Cli, project: Path) -> None:
    t0 = time.monotonic()
    rc, _out, _err = cli.run(
        "setups", "put", "x", "--project", str(project), "--analysis", "erc", "--config", "{}",
        "--interactive", timeout=10,
    )  # fmt: skip
    assert rc == 7 and time.monotonic() - t0 < 10


def test_deterministic_stdout(cli: Cli, project: Path) -> None:
    outs = []
    for _ in range(2):
        rc, out, _ = cli.run("board", "nets", "--project", str(project), "--json")
        assert rc == 0
        outs.append(json.dumps(mask_volatile(json.loads(out))))
    assert outs[0] == outs[1]
    # byte-identical raw stdout too (nothing volatile in board views)
    a = cli.run("board", "summary", "--project", str(project), "--json")[1]
    b = cli.run("board", "summary", "--project", str(project), "--json")[1]
    assert a == b


def test_pagination_via_cli(cli: Cli, project: Path) -> None:
    _, full = cli.json("board", "nets", "--project", str(project), "--limit", "500")
    names: list[str] = []
    off = 0
    while off is not None:
        _, page = cli.json(
            "board", "nets", "--project", str(project), "--offset", str(off), "--limit", "5"
        )
        assert page["data"]["total"] == full["data"]["total"]
        names += [n["name"] for n in page["data"]["items"]]
        off = page["data"]["next_offset"]
    assert names == [n["name"] for n in full["data"]["items"]]


def test_every_command_emits_valid_envelope_and_examples_run(cli: Cli, project: Path) -> None:
    from oa_service.catalog import builtin_commands

    env_schema = json.loads((SCHEMAS / "oa-envelope.v1.json").read_text(encoding="utf-8"))
    _, opened = cli.json("open", str(project))
    pid = opened["data"]["project_id"]
    _, run = cli.json("erc", "--project", pid, "--executor", "inline")
    job = run["data"]["job"]
    _, viol = cli.json("results", "violations", job["result_id"], "--limit", "1")
    cli.json(
        "setups", "put", "ex", "--project", pid, "--analysis", "erc", "--config", "{}", "--yes"
    )
    sub = {
        "$PROJECT_ID": pid, "$RESULT_ID": job["result_id"], "$JOB_ID": job["job_id"],
        "$BOARD": str(BOARDS / "microstrip" / "board.kicad_pcb"), "$NET": "USB_D+",
        "$SETUP": "ex", "$ANALYSIS": "dummy.work",
        "$FINGERPRINT": viol["data"]["items"][0]["fingerprint"],
        "$BOARD_DIR": str(project.parent / "scaffold"),
    }  # fmt: skip

    def fill(v: Any) -> Any:
        if isinstance(v, str):
            return sub.get(v, v)
        if isinstance(v, dict):
            return {k: fill(x) for k, x in v.items()}
        return v

    defs = builtin_commands(None)
    assert len(defs) >= 20
    for d in defs:
        examples = d.spec.examples or ({},)
        for ex in examples:
            rc, out, err = cli.run(*d.spec.cli, "--args", json.dumps(fill(ex)), "--json")
            lines = out.splitlines()
            assert len(lines) == 1, (d.spec.name, out, err)
            doc = json.loads(lines[0])
            jsonschema.validate(doc, env_schema)
            assert doc["ok"] is (rc < 2), (d.spec.name, rc, doc["errors"])
            codes = {e["code"] for e in doc["errors"]}
            assert not ({"SETUP_INVALID", "USAGE"} & codes), (d.spec.name, ex, doc["errors"])
            if doc["ok"] and d.spec.output_model.startswith("oa."):
                out_schema = json.loads(
                    (
                        SCHEMAS / ("commands/" + d.spec.output_model.replace("/", ".v") + ".json")
                    ).read_text("utf-8")
                )
                jsonschema.validate(doc["data"], out_schema)


def test_describe_and_schema(cli: Cli) -> None:
    _, doc = cli.json("describe", "--analyses", "--rules")
    names = [c["name"] for c in doc["data"]["commands"]]
    assert names == sorted(names) and "erc.run" in names and "setups.put" in names
    assert {a["kind"] for a in doc["data"]["analyses"]} >= {"erc", "dummy.work"}
    assert doc["data"]["error_codes"]["CONFIRMATION_REQUIRED"] == 7
    _, one = cli.json("describe", "erc.run")
    assert len(one["data"]["commands"]) == 1
    _, lst = cli.json("schema", "--list")
    listed = {Path(e["file"]).name for e in lst["data"]["entries"] if "commands/" in e["file"]}
    on_disk = {p.name for p in (SCHEMAS / "commands").glob("*.json")}
    assert listed == on_disk
    _, one_schema = cli.json("schema", "oa-envelope/1")
    assert one_schema["data"]["schema_doc"]["$id"] == "oa-envelope/1"


def test_doctor_is_fast_and_offline(cli: Cli) -> None:
    t0 = time.monotonic()
    rc, doc = cli.json("doctor")
    assert rc == 0 and doc["data"]["report"]["offline"] is True
    # process start-up dominates on slow disks; the command itself must stay well under 3 s
    assert time.monotonic() - t0 < 8.0


def test_jobs_cross_process_and_progress(cli: Cli, project: Path) -> None:
    rc, doc = cli.json(
        "run", str(project), "--analysis", "dummy.work", "--config", '{"n_tasks": 3, "work": 10}',
        "--executor", "inline", "--no-wait",
    )  # fmt: skip
    assert rc == 0
    jid = doc["data"]["job"]["job_id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        rc, st = cli.json("jobs", "status", jid, "--wait-s", "2")
        if st["data"]["status"]["state"] in ("succeeded", "failed", "cancelled"):
            break
    assert st["data"]["status"]["state"] == "succeeded"
    rc, out, err = cli.run(
        "run", str(project), "--analysis", "dummy.work", "--config", '{"n_tasks": 2, "work": 10}',
        "--executor", "inline", "--progress", "json", "--json",
    )  # fmt: skip
    assert rc == 0 and len(out.splitlines()) == 1
    events = [json.loads(line) for line in err.splitlines() if line.startswith("{")]
    assert events and [e["seq"] for e in events] == sorted(e["seq"] for e in events)


def test_init_and_ingest(cli: Cli, tmp_path: Path) -> None:
    d = tmp_path / "newproj"
    shutil.copytree(BOARDS / "microstrip", d)
    rc, doc = cli.json("init", str(d))
    assert rc == 7  # a write without --yes
    rc, doc = cli.json("init", str(d), "--yes")
    assert rc == 0 and (d / "oa-project.yaml").is_file() and (d / "oa" / "intent.yaml").is_file()
    rc, doc = cli.json("open", str(d))
    assert rc == 0 and doc["data"]["ephemeral"] is False
    out = tmp_path / "b.oab.json"
    rc, doc = cli.json("ingest", str(BOARDS / "microstrip" / "board.kicad_pcb"), "--out", str(out))
    assert rc == 0 and out.is_file()


def test_catalog_registry_helper_importable() -> None:
    sys.path.insert(0, str(SERVICE_TESTS))
    try:
        import service_helpers

        assert isinstance(service_helpers.make_registry().analyses, dict)
    finally:
        sys.path.remove(str(SERVICE_TESTS))
    assert Registry is not None
