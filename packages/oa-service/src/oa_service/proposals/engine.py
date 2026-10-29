# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Propose and apply KiCad rule changes (ai-integration.md section 10.3).

Propose is pure with respect to user files: it reads them, computes new content in memory and
stores the proposal under ``<project>/.oa/proposals/``. Apply is the only writer. It needs the
process switch (``allow_writes``), the single-use confirm token (or, for the CLI/UI, explicit
human confirmation), an unexpired proposal and unchanged base files. It writes atomically, backs
up under ``.oa/backups/<timestamp>/`` and re-opens the project.
"""

from __future__ import annotations

import difflib
import hashlib
import hmac
import importlib
import json
import os
import re
import secrets
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from oa_model.units import parse_quantity
from oa_results.violations import Violation, ViolationSet
from oa_service.commands import CallContext, CommandDef, CommandSpec, Outcome, require_write
from oa_service.errors import ServiceError, close_matches
from oa_service.proposals import kicad_dru, kicad_pro
from oa_service.proposals.models import (
    AppliedFile,
    ApplyIn,
    ApplyOut,
    AssignNetclassChange,
    ChangeProposal,
    ChangeRationale,
    CustomRuleChange,
    FileChange,
    NetclassParamChange,
    NotApplicable,
    ProposeIn,
    RemoveCustomRuleChange,
    RuleChange,
)

if TYPE_CHECKING:
    from oa_service.service import OAService

TTL = timedelta(hours=24)
_SECRET = secrets.token_bytes(32)
"""Per-process HMAC secret: a token is only valid in the process that issued it."""
_HUMAN_CALLERS = ("cli", "ui")


def now() -> datetime:
    """Current UTC time (a seam for tests)."""
    return datetime.now(UTC)


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _token(proposal_id: str, files: list[dict[str, Any]]) -> str:
    msg = json.dumps(
        [proposal_id, [[f["path"], f["sha256_base"], f["sha256_new"]] for f in files]],
        sort_keys=True,
    )
    return hmac.new(_SECRET, msg.encode(), "sha256").hexdigest()


def _workspace(svc: OAService, session: Any) -> Path:
    root = getattr(svc, "root_of", None)
    if callable(root):
        return Path(root(session))
    return Path(tempfile.gettempdir()) / "oa-proposals" / str(session.id)


def _quantity_text(constraint: str, v: float | str | None) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        if not re.fullmatch(r"\s*\d+(\.\d+)?\s*(mm|mil|in|um|ps|ns)?\s*", v):
            raise ServiceError(
                "SETUP_INVALID",
                f"Cannot use {v!r} as a rule value.",
                path="/changes",
                hint="Use a number with a unit such as '0.2mm' or '50ps', or an SI float.",
            )
        return v.replace(" ", "")
    if constraint == "via_count":
        return str(int(v))
    if constraint == "skew":
        return f"{v * 1e12:.4g}ps"
    return f"{v * 1e3:.4g}mm"


def _mm(v: float | str) -> float:
    try:
        return parse_quantity(v, "length") * 1e3
    except ValueError as exc:
        raise ServiceError(
            "SETUP_INVALID", str(exc), path="/changes", hint="Use e.g. '0.2mm' or metres."
        ) from exc


def _violations(bundle: Any) -> list[Violation]:
    return [v for i in bundle.items if isinstance(i.data, ViolationSet) for v in i.data.violations]


def _has_dru_action(v: Violation) -> bool:
    return any(
        isinstance(a.value, dict) and a.value.get("applies_via") == "dru_rule"
        for a in (v.remediation.actions if v.remediation else ())
    )


def _import_dru() -> Any:
    try:  # analyses are reached through importlib, never imported statically
        return importlib.import_module("oa_erc.dru")
    except ImportError as exc:
        raise ServiceError(
            "ANALYSIS_UNAVAILABLE",
            "The ERC package (oa-erc) is not installed, so violations cannot be mapped to rules.",
            hint="Install oa-erc, or pass explicit `changes`.",
        ) from exc


def _parse_new_rule(text: str, name: str) -> CustomRuleChange:
    block = next(b for b in kicad_dru.parse_rules(text) if b.name == name)
    raw = text[block.start : block.end]
    cond = re.search(r'\(condition "((?:[^"\\]|\\.)*)"\)', raw)
    cons = re.search(r"\(constraint (\w+) \((min|max|opt) ([^)]+)\)\)", raw)
    assert cons is not None  # rules written by propose_dru always carry a constraint
    fields: dict[str, Any] = {
        "kind": "custom_rule",
        "name": name,
        "condition": cond.group(1).replace('\\"', '"') if cond else "",
        "constraint": cons.group(1),
        cons.group(2): cons.group(3),
    }
    return CustomRuleChange.model_validate(fields)


def _diff(path: Path, old: str | None, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            (old or "").splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="a/" + path.name,
            tofile="b/" + path.name,
        )
    )


def _read(path: Path) -> tuple[str | None, str | None]:
    if not path.is_file():
        return None, None
    raw = path.read_bytes()
    return raw.decode("utf-8"), _sha(raw)


def propose(svc: OAService, a: ProposeIn, ctx: CallContext) -> Outcome:
    """Handler of ``propose.rules``."""
    session = svc.project(a.project_id)
    board = Path(session.handle().board_path)
    dru_path = ctx.check_path(board.with_suffix(".kicad_dru"))
    pro_path = ctx.check_path(board.with_suffix(".kicad_pro"))
    dru_old, dru_sha = _read(dru_path)
    pro_old, pro_sha = _read(pro_path)
    dru_text = dru_old or ""
    pro_doc: dict[str, Any] | None = kicad_pro.load(pro_old) if pro_old is not None else None

    changes: list[RuleChange] = []
    rationale: list[ChangeRationale] = []
    not_applicable: list[NotApplicable] = []
    warnings = []

    if a.from_violations is not None:
        want = set(a.from_violations.fingerprints)
        vs = [v for v in _violations(svc.result(a.from_violations.result_id)) if v.waived is None]
        known = {v.fingerprint for v in vs}
        missing = sorted(want - known)
        if missing:
            raise ServiceError(
                "NOT_FOUND",
                f"No violation with fingerprint {missing[0]!r} in {a.from_violations.result_id}.",
                path="/from_violations/fingerprints",
                hint="Fingerprints come from list_violations.",
                details={"candidates": close_matches(missing[0], known)},
            )
        if want:
            vs = [v for v in vs if v.fingerprint in want]
        for v in vs:
            for act in v.remediation.actions if v.remediation else ():
                val = act.value if isinstance(act.value, dict) else {}
                via = str(val.get("applies_via", ""))
                if via == "board_edit" or via in ("stackup", "setup", "waiver"):
                    not_applicable.append(
                        NotApplicable(
                            fingerprint=v.fingerprint,
                            rule_id=v.rule_id,
                            description=str(val.get("description", act.note or act.field)),
                            applies_via=via,
                        )
                    )
        if a.target != "kicad_pro_netclasses":
            dru = _import_dru()
            prop = dru.propose_dru(vs, dru_path, existing=dru_old if dru_old is not None else None)
            if prop.rule_names:
                dru_text = prop.text
                for name in prop.rule_names:
                    rule_change = _parse_new_rule(dru_text, name)
                    owners = [
                        v for v in vs if name.startswith(f"OA_{v.rule_id}_") and _has_dru_action(v)
                    ]
                    changes.append(rule_change)
                    rationale.append(
                        ChangeRationale(
                            change_index=len(changes) - 1,
                            fingerprints=tuple(sorted(v.fingerprint for v in owners)),
                            rule_id=owners[0].rule_id if owners else None,
                            explanation=f"Rule {name} enforces the suggested limit from "
                            f"{owners[0].rule_id if owners else 'the violations'}.",
                        )
                    )

    for ch in a.changes:
        changes.append(ch)
        if isinstance(ch, CustomRuleChange):
            if a.target == "kicad_pro_netclasses":
                raise _target_error(ch.kind, a.target)
            dru_text, _ = kicad_dru.remove_rule(dru_text, ch.name)
            dru_text = kicad_dru.append_rule(
                dru_text,
                kicad_dru.render_rule(
                    ch.name,
                    ch.condition,
                    ch.constraint,
                    min=_quantity_text(ch.constraint, ch.min),
                    opt=_quantity_text(ch.constraint, ch.opt),
                    max=_quantity_text(ch.constraint, ch.max),
                    layer=ch.layer,
                    severity=ch.severity,
                ),
            )
        elif isinstance(ch, RemoveCustomRuleChange):
            if a.target == "kicad_pro_netclasses":
                raise _target_error(ch.kind, a.target)
            dru_text, removed = kicad_dru.remove_rule(dru_text, ch.name)
            if not removed:
                names = [b.name for b in kicad_dru.parse_rules(dru_text)]
                raise ServiceError(
                    "NOT_FOUND",
                    f"No rule {ch.name!r} in {dru_path.name}.",
                    path="/changes",
                    hint="Existing rules: " + (", ".join(names) or "none") + ".",
                    details={"candidates": close_matches(ch.name, names)},
                )
        else:
            if a.target == "kicad_dru":
                raise _target_error(ch.kind, a.target)
            if pro_doc is None:
                raise ServiceError(
                    "NOT_FOUND",
                    f"No {pro_path.name} next to the board.",
                    path="/changes",
                    hint="Net class changes edit the .kicad_pro; open a KiCad project directory.",
                )
            if isinstance(ch, NetclassParamChange):
                kicad_pro.set_netclass_param(pro_doc, ch.netclass, ch.param, _mm(ch.value))
            elif isinstance(ch, AssignNetclassChange):
                kicad_pro.assign_netclass(pro_doc, list(ch.nets), ch.netclass)

    pro_text = kicad_pro.dump(pro_doc) if pro_doc is not None and pro_old is not None else None
    if pro_text is not None and not any(
        isinstance(c, NetclassParamChange | AssignNetclassChange) for c in changes
    ):
        pro_text = None

    pid = "prop_" + f"{int(now().timestamp() * 1000):011x}" + secrets.token_hex(4)
    files: list[dict[str, Any]] = []
    for path, old, sha, new in (
        (dru_path, dru_old, dru_sha, dru_text),
        (pro_path, pro_old, pro_sha, pro_text),
    ):
        if new is None or new == (old or ""):
            continue
        files.append(
            {
                "path": str(path),
                "sha256_base": sha,
                "unified_diff": _diff(path, old, new),
                "new_size": len(new.encode()),
                "new_text": new,
                "sha256_new": _sha(new.encode()),
            }
        )
    created = now()
    expires = _iso(created + TTL)
    token = _token(pid, files)
    public = ChangeProposal(
        proposal_id=pid,
        project_id=a.project_id,
        files=tuple(
            FileChange(
                path=f["path"],
                sha256_base=f["sha256_base"],
                unified_diff=f["unified_diff"],
                new_size=f["new_size"],
            )
            for f in files
        ),
        changes=tuple(changes),
        rationale=tuple(rationale),
        not_applicable=tuple(not_applicable),
        confirm_token=token,
        expires_utc=expires,
        text=(
            f"Proposal {pid} (dry run): {len(files)} file(s) would change, "
            f"{len(changes)} change(s), {len(not_applicable)} action(s) need a human."
            if files
            else f"Proposal {pid}: nothing to change in the rule files; "
            f"{len(not_applicable)} action(s) need a human."
        ),
    )
    store = _workspace(svc, session) / ".oa" / "proposals"
    store.mkdir(parents=True, exist_ok=True)
    record = {
        "proposal": json.loads(public.model_dump_json()),
        "files": files,
        "used": False,
    }
    tmp = store / f"{pid}.json.tmp"
    tmp.write_text(json.dumps(record, indent=1), encoding="utf-8", newline="\n")
    os.replace(tmp, store / f"{pid}.json")
    if (
        pro_old is not None
        and pro_text is not None
        and kicad_pro.dump(kicad_pro.load(pro_old)) != pro_old
    ):
        from oa_service.errors import ErrorInfo

        warnings.append(
            ErrorInfo(
                code="PROPOSAL_REFORMATTED",
                message=f"{pro_path.name} is not in KiCad's canonical JSON layout; the diff "
                "includes whitespace changes.",
                hint="Review the diff before applying.",
            )
        )
    return Outcome(public, tuple(warnings))


def _target_error(kind: str, target: str) -> ServiceError:
    return ServiceError(
        "USAGE",
        f"Change kind {kind!r} does not fit target {target!r}.",
        path="/target",
        hint="Use target='auto' or split the proposal.",
    )


def _find_record(svc: OAService, proposal_id: str) -> tuple[Path, dict[str, Any], str]:
    if not re.fullmatch(r"prop_[0-9a-f]+", proposal_id):
        raise ServiceError("NOT_FOUND", f"No proposal {proposal_id!r}.", path="/proposal_id")
    for h in svc.projects():
        ws = _workspace(svc, svc.project(h.project_id))
        p = ws / ".oa" / "proposals" / f"{proposal_id}.json"
        if p.is_file():
            return p, json.loads(p.read_text("utf-8")), h.project_id
    raise ServiceError(
        "NOT_FOUND",
        f"No proposal {proposal_id!r}.",
        path="/proposal_id",
        hint="Proposals live under <project>/.oa/proposals and expire after 24 h; propose again.",
    )


def apply(svc: OAService, a: ApplyIn, ctx: CallContext) -> ApplyOut:
    """Handler of ``propose.apply``."""
    rec_path, rec, project_id = _find_record(svc, a.proposal_id)
    prop = rec["proposal"]
    files: list[dict[str, Any]] = rec["files"]
    require_write(ctx, True, what="KiCad rule files")  # process switch first
    human = a.confirm and ctx.caller in _HUMAN_CALLERS
    token_ok = bool(a.confirm_token) and hmac.compare_digest(
        a.confirm_token or "", _token(a.proposal_id, files)
    )
    if not human and not token_ok:
        details: dict[str, Any] = {
            "proposal_id": a.proposal_id,
            "files": [{"path": f["path"], "unified_diff": f["unified_diff"]} for f in files],
        }
        raise ServiceError(
            "CONFIRMATION_REQUIRED",
            "The confirm token is missing, wrong, or from another process."
            if a.confirm_token
            else "Applying a proposal needs confirmation.",
            path="/confirm_token",
            hint="Pass the confirm_token of the propose step after the user reviewed the diff"
            " (CLI: --yes).",
            details=details,
        )
    if rec["used"]:
        raise ServiceError(
            "CONFIRMATION_REQUIRED",
            "This proposal was already applied (tokens are single use).",
            path="/proposal_id",
            hint="Propose again to make further changes.",
        )
    if datetime.strptime(prop["expires_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) < now():
        raise ServiceError(
            "NOT_FOUND",
            f"Proposal {a.proposal_id} expired.",
            path="/proposal_id",
            hint="Propose again.",
        )
    if not files:
        raise ServiceError("USAGE", "The proposal changes no files.", path="/proposal_id")

    paths = [ctx.check_path(f["path"]) for f in files]
    for f, p in zip(files, paths, strict=True):
        cur = _sha(p.read_bytes()) if p.is_file() else None
        if cur != f["sha256_base"]:
            raise ServiceError(
                "STALE_BASE",
                f"{p.name} changed since the proposal.",
                path="/proposal_id",
                hint="Call propose_rule_changes again and review the new diff.",
                details={"file": str(p)},
            )
    session = svc.project(project_id)
    stamp = now().strftime("%Y%m%dT%H%M%SZ")
    backup_dir = _workspace(svc, session) / ".oa" / "backups" / stamp
    applied: list[AppliedFile] = []
    for f, p in zip(files, paths, strict=True):
        backup: str | None = None
        if p.is_file():
            backup_dir.mkdir(parents=True, exist_ok=True)
            b = backup_dir / p.name
            b.write_bytes(p.read_bytes())
            backup = str(b)
        fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
        with os.fdopen(fd, "wb") as fh:
            fh.write(f["new_text"].encode("utf-8"))
        os.replace(tmp, p)
        applied.append(
            AppliedFile(
                file=str(p),
                sha256_before=f["sha256_base"],
                sha256_after=f["sha256_new"],
                backup=backup,
            )
        )
    rec["used"] = True
    rec_path.write_text(json.dumps(rec, indent=1), encoding="utf-8", newline="\n")
    handle = svc.reload(project_id)
    return ApplyOut(
        applied=tuple(applied),
        reload=handle,
        text=f"Applied {len(applied)} file(s): " + ", ".join(Path(x.file).name for x in applied),
    )


def _spec(
    name: str,
    mcp: str,
    cli: tuple[str, ...],
    title: str,
    desc: str,
    effect: str,
    out: str,
    **kw: Any,
) -> CommandSpec:
    return CommandSpec(
        name=name,
        mcp_tool=mcp,
        cli=cli,
        title=title,
        description=desc,
        side_effect=effect,  # type: ignore[arg-type]
        idempotent=kw.get("idempotent", False),
        long_running=False,
        input_model=f"oa.cmd.{name.replace('.', '_')}.in/1",
        output_model=out,
        examples=kw.get("examples", ()),
    )


def commands() -> list[CommandDef]:
    """The ``open_analysis.commands`` entry point: ``propose.rules`` and ``propose.apply``."""
    handler_p: Callable[..., Any] = propose
    handler_a: Callable[..., Any] = apply
    return [
        CommandDef(
            _spec(
                "propose.rules",
                "propose_rule_changes",
                ("propose", "rules"),
                "Propose rule changes",
                "Dry run: compute a diff for .kicad_dru / .kicad_pro net classes from violation "
                "remediations (from_violations) and/or explicit changes. Writes no user file; "
                "stores the proposal under .oa/proposals/ (valid 24 h) and returns the diff, a "
                "single-use confirm_token and the board edits that need a human. Show the diff "
                "to the user before propose.apply.",
                "compute",
                "oa.change_proposal/1",
                examples=(
                    {
                        "project_id": "$PROJECT_ID",
                        "changes": [
                            {
                                "kind": "custom_rule",
                                "name": "oa_demo",
                                "condition": "A.NetClass == 'HS'",
                                "constraint": "clearance",
                                "min": "0.2mm",
                            }
                        ],
                    },
                ),
            ),
            ProposeIn,
            ChangeProposal,
            handler_p,
            ("project_id",),
        ),
        CommandDef(
            _spec(
                "propose.apply",
                "apply_proposal",
                ("propose", "apply"),
                "Apply a proposal",
                "A WRITE: apply a stored proposal to the KiCad rule files. Needs a process with "
                "writes enabled, the single-use confirm_token from propose.rules (CLI: --yes), an "
                "unexpired proposal and unchanged files (else STALE_BASE). Atomic, with backups "
                "under .oa/backups/. Never call without the user's approval of the diff.",
                "write",
                "oa.apply_out/1",
            ),
            ApplyIn,
            ApplyOut,
            handler_a,
            ("proposal_id",),
        ),
    ]
