# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""propose.rules / propose.apply: dry-run diffs, confirmation, staleness, backups, round trips."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from oa_results.violations import Remediation, RemediationAction, Violation, ViolationSet
from oa_service import CallContext, Registry
from oa_service.local import LocalService
from oa_service.proposals import engine, kicad_dru, kicad_pro

REPO = Path(__file__).resolve().parents[4]
BOARD = REPO / "tests" / "boards" / "kicad10" / "microstrip"
DRU = (
    "(version 1)\n# keep me\n"
    '(rule "mine"\n  (condition "A.NetClass == \'X\'")\n  (constraint clearance (min 0.3mm)))\n'
)

CHANGE = {
    "kind": "custom_rule",
    "name": "oa_demo",
    "condition": "A.NetClass == 'HS'",
    "constraint": "clearance",
    "min": "0.2mm",
}


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    d = tmp_path / "proj"
    d.mkdir()
    for n in ("board.kicad_pcb", "board.kicad_pro"):
        shutil.copy(BOARD / n, d / n)
    (d / "board.kicad_dru").write_text(DRU, encoding="utf-8", newline="\n")
    return d


@pytest.fixture
def svc(tmp_path: Path) -> LocalService:
    return LocalService(cache_dir=tmp_path / "cache")


def ctx(*, writes: bool = False, caller: str = "mcp") -> CallContext:
    return CallContext(caller=caller, allow_writes=writes)  # type: ignore[arg-type]


@pytest.fixture
def opened(svc: LocalService, proj: Path) -> tuple[Registry, str]:
    reg = Registry.load(svc)
    env = reg.dispatch("board.open", {"path": str(proj / "board.kicad_pcb")}, ctx())
    assert env.ok, env.errors
    return reg, env.data.project_id


def propose(reg: Registry, pid: str, **kw: Any) -> Any:
    env = reg.dispatch("propose.rules", {"project_id": pid, **kw}, ctx())
    assert env.ok, env.errors
    return env.data


def test_dru_edits_preserve_untouched_rules_byte_for_byte() -> None:
    new = kicad_dru.append_rule(
        DRU, kicad_dru.render_rule("a", "A.Type == 'Via'", "clearance", min="0.1mm")
    )
    assert new.startswith(DRU)
    assert [b.name for b in kicad_dru.parse_rules(new)] == ["mine", "a"]
    back, removed = kicad_dru.remove_rule(new, "a")
    assert removed and back == DRU
    assert kicad_dru.remove_rule(DRU, "nope") == (DRU, False)
    assert kicad_dru.append_rule("", "(rule x)\n").startswith("(version 1)\n")


def test_dru_scanner_ignores_parens_in_strings_and_comments() -> None:
    text = (
        '(version 1)\n# (rule "fake"\n'
        '(rule "p)q" (condition "A.Name == \'a)\'") (constraint skew (max 5ps)))\n'
    )
    assert [b.name for b in kicad_dru.parse_rules(text)] == ["p)q"]


def test_kicad_pro_edit_keeps_key_order_and_other_content() -> None:
    text = (BOARD / "board.kicad_pro").read_text(encoding="utf-8")
    doc = kicad_pro.load(text)
    keys = list(doc)
    kicad_pro.set_netclass_param(doc, "Default", "clearance", 0.25)
    out = kicad_pro.dump(doc)
    new = json.loads(out)
    assert list(new) == keys
    assert new["net_settings"]["classes"][0]["clearance"] == 0.25
    other = {k: v for k, v in new.items() if k != "net_settings"}
    assert other == {k: v for k, v in json.loads(text).items() if k != "net_settings"}


def test_propose_is_a_dry_run_and_returns_a_diff(opened: tuple[Registry, str], proj: Path) -> None:
    reg, pid = opened
    before = {p.name: p.read_bytes() for p in proj.iterdir()}
    data = propose(
        reg,
        pid,
        changes=[
            CHANGE,
            {
                "kind": "netclass_param",
                "netclass": "Default",
                "param": "clearance",
                "value": "0.25mm",
            },
        ],
    )
    assert {p.name: p.read_bytes() for p in proj.iterdir()} == before
    names = sorted(Path(f.path).name for f in data.files)
    assert names == ["board.kicad_dru", "board.kicad_pro"]
    dru = next(f for f in data.files if f.path.endswith(".kicad_dru"))
    assert '+(rule "oa_demo"' in dru.unified_diff and "(min 0.2mm)" in dru.unified_diff
    assert dru.sha256_base
    assert data.confirm_token and data.expires_utc.endswith("Z")
    assert len(data.changes) == 2


def test_apply_refused_without_allow_writes(opened: tuple[Registry, str]) -> None:
    reg, pid = opened
    p = propose(reg, pid, changes=[CHANGE])
    env = reg.dispatch(
        "propose.apply", {"proposal_id": p.proposal_id, "confirm_token": p.confirm_token}, ctx()
    )
    assert not env.ok and env.errors[0].code == "PERMISSION_DENIED"


def test_apply_needs_confirmation_and_never_leaks_the_token(
    opened: tuple[Registry, str], proj: Path
) -> None:
    reg, pid = opened
    p = propose(reg, pid, changes=[CHANGE])
    env = reg.dispatch("propose.apply", {"proposal_id": p.proposal_id}, ctx(writes=True))
    e = env.errors[0]
    assert e.code == "CONFIRMATION_REQUIRED"
    assert "oa_demo" in json.dumps(e.details)  # the diff is shown for the human to review
    assert p.confirm_token not in json.dumps(e.details)
    bad = reg.dispatch(
        "propose.apply", {"proposal_id": p.proposal_id, "confirm_token": "x" * 64}, ctx(writes=True)
    )
    assert bad.errors[0].code == "CONFIRMATION_REQUIRED"
    # confirm=true alone is not enough for an agent caller
    flag = reg.dispatch(
        "propose.apply", {"proposal_id": p.proposal_id, "confirm": True}, ctx(writes=True)
    )
    assert flag.errors[0].code == "CONFIRMATION_REQUIRED"
    assert (proj / "board.kicad_dru").read_text(encoding="utf-8") == DRU


def test_apply_once_with_token_backup_and_reload(opened: tuple[Registry, str], proj: Path) -> None:
    reg, pid = opened
    p = propose(reg, pid, changes=[CHANGE])
    args = {"proposal_id": p.proposal_id, "confirm_token": p.confirm_token}
    env = reg.dispatch("propose.apply", args, ctx(writes=True))
    assert env.ok, env.errors
    out = env.data
    text = (proj / "board.kicad_dru").read_text(encoding="utf-8")
    assert text.startswith(DRU) and '(rule "oa_demo"' in text
    f = out.applied[0]
    assert f.sha256_before != f.sha256_after
    assert f.backup and Path(f.backup).read_text(encoding="utf-8") == DRU
    assert out.reload.project_id == pid
    again = reg.dispatch("propose.apply", args, ctx(writes=True))
    assert again.errors[0].code == "CONFIRMATION_REQUIRED"
    assert (proj / "board.kicad_dru").read_text(encoding="utf-8") == text


def test_cli_human_confirmation_without_token(opened: tuple[Registry, str], proj: Path) -> None:
    reg, pid = opened
    p = propose(reg, pid, changes=[CHANGE])
    env = reg.dispatch(
        "propose.apply",
        {"proposal_id": p.proposal_id, "confirm": True},
        ctx(writes=True, caller="cli"),
    )
    assert env.ok, env.errors


def test_stale_base_when_file_changes_between_propose_and_apply(
    opened: tuple[Registry, str], proj: Path
) -> None:
    reg, pid = opened
    p = propose(reg, pid, changes=[CHANGE])
    (proj / "board.kicad_dru").write_text(DRU + "# edited\n", encoding="utf-8")
    env = reg.dispatch(
        "propose.apply",
        {"proposal_id": p.proposal_id, "confirm_token": p.confirm_token},
        ctx(writes=True),
    )
    assert env.errors[0].code == "STALE_BASE"
    assert "# edited" in (proj / "board.kicad_dru").read_text(encoding="utf-8")


def test_expired_proposal(opened: tuple[Registry, str], monkeypatch: pytest.MonkeyPatch) -> None:
    reg, pid = opened
    p = propose(reg, pid, changes=[CHANGE])
    real = engine.now
    monkeypatch.setattr(engine, "now", lambda: real() + timedelta(hours=25))
    env = reg.dispatch(
        "propose.apply",
        {"proposal_id": p.proposal_id, "confirm_token": p.confirm_token},
        ctx(writes=True),
    )
    assert env.errors[0].code == "NOT_FOUND" and "expired" in env.errors[0].message


def test_unknown_netclass_and_rule_report_candidates(opened: tuple[Registry, str]) -> None:
    reg, pid = opened
    env = reg.dispatch(
        "propose.rules",
        {
            "project_id": pid,
            "changes": [
                {
                    "kind": "netclass_param",
                    "netclass": "Defalt",
                    "param": "clearance",
                    "value": 0.0002,
                }
            ],
        },
        ctx(),
    )
    assert env.errors[0].code == "NOT_FOUND" and "Default" in env.errors[0].details["candidates"]
    env = reg.dispatch(
        "propose.rules",
        {"project_id": pid, "changes": [{"kind": "remove_custom_rule", "name": "mien"}]},
        ctx(),
    )
    assert "mine" in env.errors[0].details["candidates"]


def test_remove_custom_rule_roundtrip(opened: tuple[Registry, str], proj: Path) -> None:
    reg, pid = opened
    p = propose(reg, pid, changes=[{"kind": "remove_custom_rule", "name": "mine"}])
    assert "-(rule" in p.files[0].unified_diff


def _violation(fp: str, via: str, **extra: Any) -> Violation:
    action = {
        "kind": "change_rule" if via == "dru_rule" else "reroute",
        "description": "Widen the gap." if via == "dru_rule" else "Reroute the trace.",
        "targets": ["SIG"],
        "parameter": "clearance",
        "suggested_value": 0.0002,
        "applies_via": via,
        **extra,
    }
    return Violation(
        rule_id="IM-002",
        severity="error",
        message="m",
        nets=("SIG",),
        location=(0.0, 0.0),
        fingerprint=fp,
        remediation=Remediation(
            hint="h", actions=(RemediationAction(field=action["kind"], value=action, note="n"),)
        ),
    )


def test_from_violations_maps_dru_actions_and_lists_board_edits(
    opened: tuple[Registry, str], svc: LocalService, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("oa_erc")
    reg, pid = opened
    vs = ViolationSet.from_violations(
        ("IM-002",), (_violation("fp_dru", "dru_rule"), _violation("fp_edit", "board_edit"))
    )
    bundle = SimpleNamespace(items=[SimpleNamespace(data=vs)])
    result: Callable[[str], Any] = lambda rid: bundle  # noqa: E731
    monkeypatch.setattr(svc, "result", result)
    data = propose(reg, pid, from_violations={"result_id": "r1"})
    assert [Path(f.path).name for f in data.files] == ["board.kicad_dru"]
    assert "OA_IM-002_clearance_SIG" in data.files[0].unified_diff
    assert data.changes[0].kind == "custom_rule" and data.changes[0].constraint == "clearance"
    assert data.rationale[0].fingerprints == ("fp_dru",)
    assert [n.fingerprint for n in data.not_applicable] == ["fp_edit"]
    env = reg.dispatch(
        "propose.rules",
        {"project_id": pid, "from_violations": {"result_id": "r1", "fingerprints": ["nope"]}},
        ctx(),
    )
    assert env.errors[0].code == "NOT_FOUND"


def test_paths_outside_roots_are_refused(opened: tuple[Registry, str], tmp_path: Path) -> None:
    reg, pid = opened
    c = CallContext(caller="mcp", roots=(tmp_path / "elsewhere",))
    env = reg.dispatch("propose.rules", {"project_id": pid, "changes": [CHANGE]}, c)
    assert env.errors[0].code == "PERMISSION_DENIED"
