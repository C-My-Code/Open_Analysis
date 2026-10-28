# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The command catalog over FakeService (the WP-210 contract)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from oa_service import CallContext, Envelope, Registry, envelope_json, mask_volatile
from oa_service.schemas import check_schemas, schema_for, write_schemas
from oa_service.testing import FakeService


def call(reg: Registry, name: str, args: dict[str, Any], ctx: CallContext) -> Envelope[Any]:
    return reg.dispatch(name, args, ctx)


def test_catalog_names_and_order(reg: Registry) -> None:
    names = [s.name for s in reg.specs()]
    assert names == sorted(names)
    required = {
        "meta.describe", "meta.schema", "board.open", "board.summary", "board.nets", "board.net",
        "board.stackup", "impedance.xs", "impedance.profile", "erc.run", "results.violations",
        "results.explain", "analysis.run", "jobs.status", "jobs.cancel", "results.get",
        "results.summary", "results.export",
    }  # fmt: skip
    assert required <= set(names)
    tools = [s.mcp_tool for s in reg.specs() if s.mcp_tool]
    assert len(tools) == len(set(tools))
    assert all(len(s.description) <= 1024 for s in reg.specs())


def test_envelope_shape(reg: Registry, pid: str, ctx: CallContext) -> None:
    env = call(reg, "board.summary", {"project_id": pid}, ctx)
    doc = json.loads(envelope_json(env))
    assert list(doc) == ["schema", "version", "ok", "data", "warnings", "errors"]
    assert doc["ok"] is True and doc["schema"] == "oa.board_summary/1"
    assert doc["data"]["layer_count"] == 2  # diff_pair fixture


def test_unknown_command_and_bad_input(reg: Registry, pid: str, ctx: CallContext) -> None:
    env = call(reg, "board.sumary", {}, ctx)
    assert not env.ok and env.errors[0].code == "NOT_FOUND"
    assert "board.summary" in (env.errors[0].hint or "")
    env = call(reg, "board.nets", {"project_id": pid, "limti": 3}, ctx)
    assert not env.ok
    e = env.errors[0]
    assert e.code == "SETUP_INVALID" and e.path == "/limti" and e.hint
    env = call(reg, "board.nets", {"project_id": pid, "page": {"limit": 0}}, ctx)
    assert env.errors[0].path == "/page/limit"


def test_not_found_has_candidates(reg: Registry, pid: str, ctx: CallContext) -> None:
    env = call(reg, "board.net", {"project_id": pid, "net": "USB_DP"}, ctx)
    assert env.errors[0].code == "NOT_FOUND"
    assert env.errors[0].details["candidates"]
    env = call(reg, "board.summary", {"project_id": "p_nope"}, ctx)
    assert env.errors[0].code == "NOT_FOUND"


def test_nets_pagination_concat_equals_unpaged(reg: Registry, pid: str, ctx: CallContext) -> None:
    full = call(reg, "board.nets", {"project_id": pid, "page": {"limit": 500}}, ctx).data
    assert full is not None and full.total == len(full.items) and full.next_offset is None
    got, off = [], 0
    while off is not None:
        page = call(
            reg, "board.nets", {"project_id": pid, "page": {"offset": off, "limit": 2}}, ctx
        ).data
        assert page.total == full.total
        got += list(page.items)
        off = page.next_offset
    assert got == list(full.items)
    assert [n.name for n in got] == sorted(n.name for n in got)


def test_stackup_frequency_quantity(reg: Registry, pid: str, ctx: CallContext) -> None:
    env = call(reg, "board.stackup", {"project_id": pid, "frequency": "2GHz"}, ctx)
    assert env.ok and env.data.frequency_hz == 2e9
    assert any(layer.dk for layer in env.data.layers)


def test_erc_flow_and_violations(reg: Registry, pid: str, ctx: CallContext) -> None:
    env = call(reg, "erc.run", {"project_id": pid, "wait_s": 1}, ctx)
    assert env.ok and env.data.job.state == "succeeded"
    rid = env.data.job.result_id
    s = call(reg, "results.summary", {"result_id": rid}, ctx)
    assert s.ok and len(json.dumps(s.data.model_dump(mode="json"))) <= 8192
    page = call(reg, "results.violations", {"result_id": rid, "page": {"limit": 2}}, ctx).data
    assert page.total == 3 and page.next_offset == 2
    assert [v.severity for v in page.items] == ["error", "warning"]
    tail = call(reg, "results.violations", {"result_id": rid, "page": {"offset": 2}}, ctx).data
    assert len(tail.items) == 1 and tail.next_offset is None
    only = call(reg, "results.violations", {"result_id": rid, "severity_min": "error"}, ctx).data
    assert only.total == 1
    full = call(reg, "results.violations", {"result_id": rid, "fields": "full"}, ctx).data
    assert full.items[0].remediation is not None
    fp = page.items[0].fingerprint
    ex = call(reg, "results.explain", {"result_id": rid, "fingerprint": fp}, ctx)
    assert ex.ok and "fingerprint" in ex.data.waiver_yaml and ex.data.margin is not None
    bad = call(reg, "results.explain", {"result_id": rid, "fingerprint": "zz"}, ctx)
    assert bad.errors[0].code == "NOT_FOUND"


def test_determinism_with_volatile_masked(fake: FakeService, ctx: CallContext) -> None:
    outs = []
    for _ in range(2):
        svc = FakeService(workdir=fake.workdir)
        reg = Registry(svc)
        p = svc.open_fixture("microstrip").project_id
        env = reg.dispatch("erc.run", {"project_id": p, "wait_s": 1}, ctx)
        rid = env.data.job.result_id
        v = reg.dispatch("results.violations", {"result_id": rid}, ctx)
        doc = json.loads(envelope_json(v))
        outs.append(json.dumps(mask_volatile(doc), sort_keys=False))
    assert outs[0] == outs[1]


def test_dry_run_budget_and_clamp(fake: FakeService, ctx: CallContext) -> None:
    reg = Registry(fake)
    p = fake.open_fixture("microstrip").project_id
    fake.estimate_seconds = 500.0
    dry = reg.dispatch("erc.run", {"project_id": p, "dry_run": True}, ctx)
    assert dry.ok and dry.data.dry_run and dry.data.job is None
    assert dry.data.estimate is not None and dry.data.estimate.est_seconds == 500.0
    capped = ctx.model_copy(
        update={"default_budget": ctx.budget_ceiling.model_copy(update={"max_runtime_s": 60.0})}
    )
    env = reg.dispatch("erc.run", {"project_id": p}, capped)
    assert not env.ok and env.errors[0].code == "BUDGET_EXCEEDED"
    assert env.errors[0].details["estimate"]["est_seconds"] == 500.0
    # a request above the ceiling is clamped, with a warning
    ceil = ctx.model_copy(
        update={"budget_ceiling": ctx.budget_ceiling.model_copy(update={"max_runtime_s": 10.0})}
    )
    fake.estimate_seconds = 1.0
    env = reg.dispatch(
        "erc.run", {"project_id": p, "budget": {"max_runtime_s": 9999}, "wait_s": 1}, ceil
    )
    assert env.ok and [w.code for w in env.warnings] == ["BUDGET_CLAMPED"]


def test_job_polling_and_cancel(ctx: CallContext, tmp_path: Path) -> None:
    svc = FakeService(auto_complete=False, workdir=tmp_path)
    reg = Registry(svc)
    p = svc.open_fixture("microstrip").project_id
    h = reg.dispatch("erc.run", {"project_id": p, "wait_s": 0}, ctx).data.job
    assert h.state == "running"
    st = reg.dispatch("jobs.status", {"job_id": h.job_id, "events_after_seq": 0}, ctx).data
    assert st.status.state == "running" and st.events
    c = reg.dispatch("jobs.cancel", {"job_id": h.job_id}, ctx)
    assert c.data.state == "cancelled" and c.data.cancel_reason == "user"
    h2 = reg.dispatch("erc.run", {"project_id": p, "wait_s": 0}, ctx).data.job
    svc.finish_job(h2.job_id)
    st2 = reg.dispatch("jobs.status", {"job_id": h2.job_id}, ctx).data
    assert st2.status.state == "succeeded" and st2.summary is not None
    jl = reg.dispatch("jobs.list", {"project_id": p}, ctx).data
    assert jl.total == 2


def test_setup_validation_errors_have_pointers(reg: Registry, pid: str, ctx: CallContext) -> None:
    env = reg.dispatch(
        "analysis.run",
        {"project_id": pid, "analysis": "dummy.work", "config": {"n_taks": 3}},
        ctx,
    )
    assert not env.ok
    e = env.errors[0]
    assert e.code == "SETUP_INVALID" and e.path == "/config/n_taks" and e.hint
    dry = reg.dispatch(
        "analysis.run",
        {"project_id": pid, "analysis": "dummy.work", "config": {"n_taks": 3}, "dry_run": True},
        ctx,
    )
    assert dry.ok and not dry.data.valid and dry.data.diagnostics[0].path == "/config/n_taks"


def test_write_needs_permission_and_confirmation(reg: Registry, pid: str) -> None:
    args = {"project_id": pid, "name": "demo", "analysis": "erc", "config": {}}
    env = reg.dispatch("setups.put", args, CallContext(caller="mcp"))
    assert env.errors[0].code == "PERMISSION_DENIED"
    ctx = CallContext(caller="cli", allow_writes=True)
    env = reg.dispatch("setups.put", args, ctx)
    assert env.errors[0].code == "CONFIRMATION_REQUIRED"
    ok = reg.dispatch("setups.put", {**args, "confirm": True}, ctx)
    assert ok.ok
    assert [s.name for s in reg.dispatch("setups.list", {"project_id": pid}, ctx).data.setups] == [
        "demo"
    ]


def test_roots_enforced(tmp_path: Path, fake: FakeService, reg: Registry) -> None:
    inside = fake.fixture_path("microstrip")
    allowed = CallContext(caller="mcp", roots=(inside.parent,))
    assert reg.dispatch("board.open", {"path": str(inside)}, allowed).ok
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "x.oab.json").write_text("{}")
    env = reg.dispatch("board.open", {"path": str(other / "x.oab.json")}, allowed)
    assert env.errors[0].code == "PERMISSION_DENIED"


def test_size_cap_truncates_pages(reg: Registry, pid: str) -> None:
    ctx = CallContext(caller="mcp", max_response_bytes=1024)
    env = reg.dispatch("board.nets", {"project_id": pid, "page": {"limit": 500}}, ctx)
    assert env.ok
    assert len(env.data.model_dump_json()) <= 1024 * 2
    if env.warnings:
        w = env.warnings[0]
        assert w.code == "RESULT_TRUNCATED" and w.details["next_offset"] == env.data.next_offset


def test_get_result_and_export(reg: Registry, pid: str, ctx: CallContext, tmp_path: Path) -> None:
    rid = reg.dispatch("erc.run", {"project_id": pid, "wait_s": 1}, ctx).data.job.result_id
    man = reg.dispatch("results.get", {"result_id": rid}, ctx).data
    assert man.manifest.items[0].id == "violations"
    item = reg.dispatch("results.get", {"result_id": rid, "item_id": "violations"}, ctx)
    assert item.ok and item.data.item.summary.kind == "violations"
    ex = reg.dispatch("results.export", {"result_id": rid, "format": "sarif"}, ctx)
    assert ex.ok and Path(ex.data.path).is_file() and len(ex.data.sha256) == 64
    again = reg.dispatch("results.export", {"result_id": rid, "format": "sarif"}, ctx)
    assert again.errors[0].code == "PERMISSION_DENIED"
    bad = reg.dispatch("results.export", {"result_id": rid, "format": "touchstone"}, ctx)
    assert bad.errors[0].code == "NOT_FOUND"


def test_impedance_xs_unavailable_without_service(reg: Registry, ctx: CallContext) -> None:
    env = reg.dispatch(
        "impedance.xs",
        {"quick": {"topology": "microstrip", "w": "5mil", "h": "4mil", "t": "1.4mil", "er": 4.2}},
        ctx,
    )
    assert env.errors[0].code == "ANALYSIS_UNAVAILABLE"
    two = reg.dispatch(
        "impedance.xs",
        {"quick": {"topology": "microstrip", "w": 1, "h": 1, "t": 1, "er": 4}, "xsection": None},
        ctx,
    )
    assert two.ok is False


def test_describe_and_schema(reg: Registry, ctx: CallContext, tmp_path: Path) -> None:
    d = reg.dispatch("meta.describe", {"analyses": True, "rules": True}, ctx).data
    assert {c.name for c in d.commands} == {s.name for s in reg.specs()}
    assert d.exit_codes["7"] and d.error_codes["BUDGET_EXCEEDED"] == 6
    assert [a.kind for a in d.analyses] == sorted(a.kind for a in d.analyses)
    one = reg.dispatch("meta.describe", {"command": "erc.run"}, ctx).data
    assert len(one.commands) == 1
    sc = reg.dispatch("meta.schema", {"id": "oa.board_summary/1"}, ctx).data
    assert sc.schema_doc is not None and sc.schema_doc["$id"] == "oa.board_summary/1"
    listing = reg.dispatch("meta.schema", {}, ctx).data
    assert {"oa-envelope/1", "oa-error/1"} <= {e.id for e in listing.entries}
    write_schemas(reg, tmp_path)
    assert check_schemas(reg, tmp_path) == []
    (tmp_path / "commands" / "orphan.json").write_text("{}")
    assert check_schemas(reg, tmp_path) == ["orphan commands/orphan.json"]
    with pytest.raises(Exception, match="No schema"):
        schema_for(reg, "oa.nope/1")


def test_examples_validate_and_run(fake: FakeService, ctx: CallContext) -> None:
    reg = Registry(fake)
    pid = fake.open_fixture("diff_pair").project_id
    rid = reg.dispatch("erc.run", {"project_id": pid, "wait_s": 1}, ctx).data.job
    sub = {
        "$PROJECT_ID": pid, "$RESULT_ID": rid.result_id, "$JOB_ID": rid.job_id,
        "$BOARD": str(fake.fixture_path("microstrip")), "$NET": "P", "$SETUP": "s1",
        "$ANALYSIS": "dummy.work", "$FINGERPRINT": "x",
    }  # fmt: skip
    from oa_model.setup import AnalysisSetup

    fake.project(pid).put_setup(
        AnalysisSetup(schema="oa-setup/1", name="s1", analysis="erc", config={})
    )
    net = fake.project(pid).board.nets[0].id
    sub["$NET"] = net

    def fill(v: Any) -> Any:
        if isinstance(v, str):
            return sub.get(v, v)
        if isinstance(v, dict):
            return {k: fill(x) for k, x in v.items()}
        return v

    for d in reg.definitions():
        for ex in d.spec.examples:
            env = reg.dispatch(d.spec.name, fill(ex), ctx)
            bad = {"SETUP_INVALID", "USAGE"} & {e.code for e in env.errors}
            assert not bad, (d.spec.name, ex, env.errors)
