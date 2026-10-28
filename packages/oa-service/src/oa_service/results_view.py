# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Result rendering for transports: summaries, violation paging, explanations, items, exports.

Pure functions of a ``ResultBundle``. Everything is bounded: a ``BundleSummary`` stays under
8 KiB, an ``ItemSummary`` under 2 KiB, arrays over 1000 elements become ``ArrayRef`` values, and
every list has a documented total order (ai-integration.md sections 3.3, 4).
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from pydantic import JsonValue

from oa_model._base import OAModel
from oa_results.artifact import ArtifactRef
from oa_results.bundle import ResultBundle, ResultItem
from oa_results.tables import TableResult
from oa_results.violations import Violation, ViolationSet
from oa_service.errors import ServiceError, close_matches
from oa_service.models import (
    ArrayRef,
    BundleSummary,
    ExplainIn,
    ExportFormat,
    ExportInfo,
    ItemSummary,
    ProvenanceBrief,
    ResultGetIn,
    ResultGetOut,
    ResultItemBrief,
    ResultItemView,
    ResultManifest,
    RuleExplain,
    RuleInfo,
    ViolationBrief,
    ViolationExplanation,
    ViolationPage,
    ViolationQuery,
    ViolationView,
)
from oa_service.types import Page, paginate

__all__ = [
    "bundle_summary",
    "explain_violation",
    "export_bundle",
    "get_result",
    "item_arrays",
    "item_summary",
    "manifest",
    "violations_page",
]

BUNDLE_CAP = 8 * 1024
ITEM_CAP = 2 * 1024
MAX_INLINE = 1000
_SEV_RANK = {"error": 0, "warning": 1, "info": 2}


def _compact(obj: object) -> int:
    return len(json.dumps(obj, separators=(",", ":"), default=str))


# ---------------------------------------------------------------- summaries


def item_summary(item: ResultItem) -> ItemSummary:
    """Summary of one item, capped at 2 KiB by dropping statistics first."""
    full = item.data.summary()
    stats = full.get("stats", {})
    raw_flags = full.get("validity_flags")
    flags = (
        tuple(f for f in raw_flags if isinstance(f, dict)) if isinstance(raw_flags, list) else ()
    )
    truncated = bool(isinstance(stats, dict) and stats.get("truncated"))
    out = ItemSummary(
        item_id=item.id,
        kind=item.kind,
        title=item.title,
        accuracy_class=item.data.accuracy_class,
        digest=str(full.get("digest", "")),
        stats=stats if isinstance(stats, dict) else {},
        validity_flags=flags,
        truncated=truncated,
    )
    if _compact(out.model_dump(mode="json")) > ITEM_CAP:
        out = out.model_copy(update={"stats": {"truncated": True}, "truncated": True})
    return out


def bundle_summary(bundle: ResultBundle) -> BundleSummary:
    """Bounded summary of a bundle: first 20 items, worst 10 diagnostics, provenance."""
    counts: dict[str, int] = {}
    for d in bundle.diagnostics:
        counts[d.severity] = counts.get(d.severity, 0) + 1
    top = sorted(bundle.diagnostics, key=lambda d: (_SEV_RANK[d.severity], d.code, d.message))[:10]
    p = bundle.provenance
    items = [item_summary(i) for i in bundle.items[:20]]
    more = (
        f"get_result(result_id={bundle.id!r}, item_id=...) for items 21..{len(bundle.items)}"
        if len(bundle.items) > 20
        else None
    )
    text = " ".join(bundle.summary_text.split())
    out = BundleSummary(
        result_id=bundle.id,
        analysis=p.analysis,
        status=bundle.status,
        text=text[:1500],
        items=tuple(items),
        n_items=len(bundle.items),
        diagnostics=dict(sorted(counts.items())),
        top_diagnostics=tuple(top),
        provenance=ProvenanceBrief(
            analysis=p.analysis,
            analysis_version=p.analysis_version,
            solver=p.solver,
            solver_version=p.solver_version,
            board_hash=p.board_hash,
            physics_hash=p.physics_hash,
            setup_hash=p.setup_hash,
            inputs_hash=p.inputs_hash,
        ),
        more=more,
    )
    # Over the cap: drop trailing item statistics, then trailing items.
    while _compact(out.model_dump(mode="json")) > BUNDLE_CAP and out.items:
        last = out.items[-1]
        if last.stats and not last.truncated:
            slim = last.model_copy(update={"stats": {"truncated": True}, "truncated": True})
            out = out.model_copy(update={"items": (*out.items[:-1], slim)})
        else:
            out = out.model_copy(
                update={
                    "items": out.items[:-1],
                    "more": f"get_result(result_id={bundle.id!r}, item_id=...) for omitted items",
                }
            )
    return out


def manifest(bundle: ResultBundle) -> ResultManifest:
    return ResultManifest(
        id=bundle.id,
        status=bundle.status,
        analysis=bundle.provenance.analysis,
        summary=bundle.summary_text,
        items=tuple(
            ResultItemBrief(
                id=i.id, kind=i.kind, title=i.title, accuracy_class=i.data.accuracy_class
            )
            for i in bundle.items
        ),
    )


# ---------------------------------------------------------------- violations


def _violation_set(bundle: ResultBundle, item_id: str | None) -> tuple[ResultItem, ViolationSet]:
    sets = [i for i in bundle.items if isinstance(i.data, ViolationSet)]
    if item_id is not None:
        for i in bundle.items:
            if i.id == item_id:
                if not isinstance(i.data, ViolationSet):
                    raise ServiceError(
                        "USAGE",
                        f"Item {item_id!r} is a {i.kind} item, not violations.",
                        path="/item_id",
                        hint="Violations items: " + ", ".join(s.id for s in sets) + ".",
                    )
                return i, i.data
        raise ServiceError(
            "NOT_FOUND",
            f"No item {item_id!r} in result {bundle.id}.",
            path="/item_id",
            hint="Items: " + ", ".join(i.id for i in bundle.items) + ".",
            details={"candidates": close_matches(item_id, [i.id for i in bundle.items])},
        )
    if not sets:
        raise ServiceError(
            "NOT_FOUND",
            f"Result {bundle.id} ({bundle.provenance.analysis}) has no violations item.",
            path="/result_id",
            hint="Run an analysis that produces violations, such as erc.",
        )
    if len(sets) > 1:
        raise ServiceError(
            "AMBIGUOUS_REF",
            "The bundle has several violations items.",
            path="/item_id",
            hint="Pass item_id.",
            details={"candidates": [s.id for s in sets]},
        )
    return sets[0], sets[0].data  # type: ignore[return-value]


def _sort_key(v: Violation) -> tuple[Any, ...]:
    return (_SEV_RANK[v.severity], v.rule_id, v.nets, v.location[0], v.location[1], v.fingerprint)


def _brief(v: Violation) -> ViolationBrief:
    return ViolationBrief(
        fingerprint=v.fingerprint,
        rule_id=v.rule_id,
        severity=v.severity,
        message=v.message,
        nets=tuple(v.nets),
        layers=tuple(v.layers),
        location_m=(v.location[0], v.location[1]),
        measured=v.measured,
        limit=v.limit,
        unit=v.unit,
        fix_hint=v.remediation.hint if v.remediation is not None else v.fix_hint,
    )


def _view(v: Violation) -> ViolationView:
    bbox = None
    if v.geometry is not None and len(v.geometry.exterior):
        a = v.geometry.exterior.array
        bbox = (
            float(np.min(a[:, 0])),
            float(np.min(a[:, 1])),
            float(np.max(a[:, 0])),
            float(np.max(a[:, 1])),
        )
    return ViolationView(
        **_brief(v).model_dump(),
        items=tuple(v.items),
        geometry_bbox_m=bbox,
        remediation=v.remediation.model_dump(mode="json") if v.remediation else None,
        waived=v.waived is not None,
    )


def violations_page(bundle: ResultBundle, q: ViolationQuery) -> ViolationPage:
    """Filtered, totally ordered page of violations.

    Order: severity (error first), rule_id, nets, location x, location y, fingerprint.
    """
    _, vs = _violation_set(bundle, q.item_id)
    floor = _SEV_RANK[q.severity_min]
    sel = [v for v in vs.violations if _SEV_RANK[v.severity] <= floor]
    if not q.include_waived:
        sel = [v for v in sel if v.waived is None]
    if q.rule_id is not None:
        sel = [v for v in sel if fnmatch.fnmatchcase(v.rule_id, q.rule_id)]
    if q.net is not None:
        sel = [v for v in sel if any(fnmatch.fnmatchcase(n, q.net) for n in v.nets)]
    if q.layer is not None:
        sel = [v for v in sel if q.layer in v.layers]
    sel.sort(key=_sort_key)
    conv = _view if q.fields == "full" else _brief
    rows = [conv(v) for v in sel]
    sort = "severity desc, rule_id, nets, location.x, location.y, fingerprint"
    return ViolationPage(**paginate(rows, q.page, sort))


def explain_violation(
    bundle: ResultBundle, req: ExplainIn, rules: Mapping[str, RuleInfo]
) -> ViolationExplanation:
    """Deterministic explanation: facts, rule metadata, margin, related violations, waiver."""
    _, vs = _violation_set(bundle, req.item_id)
    hit = next((v for v in vs.violations if v.fingerprint == req.fingerprint), None)
    if hit is None:
        cands = close_matches(req.fingerprint, [v.fingerprint for v in vs.violations])
        raise ServiceError(
            "NOT_FOUND",
            f"No violation with fingerprint {req.fingerprint!r}.",
            path="/fingerprint",
            hint="List fingerprints with results.violations.",
            details={"candidates": cands},
        )
    rule = rules.get(hit.rule_id)
    if rule is None:
        for r in rules.values():
            if hit.rule_id in r.aliases:
                rule = r
    margin = margin_rel = None
    if hit.measured is not None and hit.limit is not None:
        margin = hit.measured - hit.limit
        margin_rel = margin / hit.limit if hit.limit else None
    related = sorted(
        v.fingerprint
        for v in vs.violations
        if v.fingerprint != hit.fingerprint
        and (set(v.nets) & set(hit.nets) or set(v.items) & set(hit.items))
    )[:20]
    waiver = {
        "waivers": [
            {
                "fingerprint": hit.fingerprint,
                "reason": "<state why this is acceptable>",
            }
        ]
    }
    return ViolationExplanation(
        violation=_view(hit),
        rule=RuleExplain(
            id=rule.id,
            title=rule.title,
            category=rule.category,
            aliases=rule.aliases,
            default_severity=rule.default_severity,
            references=rule.references,
            explanation=rule.explanation,
        )
        if rule is not None
        else None,
        margin=margin,
        margin_rel=margin_rel,
        nearby_items=tuple(sorted(hit.items))[:20],
        related=tuple(related),
        remediation=hit.remediation.model_dump(mode="json") if hit.remediation else None,
        waiver_yaml=yaml.safe_dump(waiver, sort_keys=False),
    )


# ---------------------------------------------------------------- items and arrays


def _walk_arrays(obj: object, prefix: str, out: dict[str, np.ndarray[Any, Any]]) -> None:
    if hasattr(obj, "array") and not isinstance(obj, OAModel):
        out[prefix] = np.asarray(obj.array)
    elif isinstance(obj, OAModel):
        label = getattr(obj, "name", None)
        for fname in type(obj).model_fields:
            if fname in ("provenance",):
                continue
            child = prefix
            if fname != "values" or label is None:
                child = f"{prefix}.{fname}" if prefix else fname
            elif label is not None:
                child = f"{prefix}.{label}" if prefix else str(label)
            _walk_arrays(getattr(obj, fname), child, out)
    elif isinstance(obj, tuple | list):
        for i, c in enumerate(obj):
            _walk_arrays(c, f"{prefix}[{i}]" if prefix else f"[{i}]", out)


def item_arrays(bundle: ResultBundle, item_id: str) -> dict[str, np.ndarray[Any, Any]]:
    """All arrays of one item by name (see :func:`get_result` for the naming)."""
    item = next((i for i in bundle.items if i.id == item_id), None)
    if item is None:
        raise ServiceError(
            "NOT_FOUND",
            f"No item {item_id!r} in result {bundle.id}.",
            path="/item_id",
            hint="Items: " + ", ".join(i.id for i in bundle.items) + ".",
        )
    arrays: dict[str, np.ndarray[Any, Any]] = {}
    _walk_arrays(item.data, "", arrays)
    return arrays


def _decimate(a: np.ndarray[Any, Any], n: int) -> np.ndarray[Any, Any]:
    if a.size <= n:
        return a
    idx = np.unique(np.linspace(0, a.size - 1, n).round().astype(int))
    return np.asarray(a[idx])


def _item_brief(item: ResultItem) -> ResultItemBrief:
    return ResultItemBrief(
        id=item.id, kind=item.kind, title=item.title, accuracy_class=item.data.accuracy_class
    )


def get_result(bundle: ResultBundle, req: ResultGetIn) -> ResultGetOut:
    """Manifest, or one item rendered compactly (decimated series, ArrayRefs, table window)."""
    man = manifest(bundle)
    if req.item_id is None:
        return ResultGetOut(manifest=man, item=None)
    item = next((i for i in bundle.items if i.id == req.item_id), None)
    if item is None:
        raise ServiceError(
            "NOT_FOUND",
            f"No item {req.item_id!r} in result {bundle.id}.",
            path="/item_id",
            hint="Items: " + ", ".join(i.id for i in bundle.items) + ".",
            details={"candidates": close_matches(req.item_id, [i.id for i in bundle.items])},
        )
    arrays: dict[str, np.ndarray[Any, Any]] = {}
    _walk_arrays(item.data, "", arrays)
    series: dict[str, list[float]] = {}
    refs: list[ArrayRef] = []
    decimated_from: int | None = None
    for name in sorted(arrays):
        if req.select and name not in req.select:
            continue
        a = arrays[name]
        uri = f"oa://results/{bundle.id}/items/{item.id}/arrays/{name}"
        if a.ndim == 1 and a.dtype.kind in "fiu":
            if a.size > req.max_points:
                decimated_from = int(a.size)
            series[name] = [float(x) for x in _decimate(a, req.max_points)]
            if a.size > req.max_points:
                refs.append(ArrayRef(name=name, shape=tuple(a.shape), dtype=str(a.dtype), uri=uri))
        elif a.size > MAX_INLINE or a.ndim != 1:
            refs.append(ArrayRef(name=name, shape=tuple(a.shape), dtype=str(a.dtype), uri=uri))
    rows = None
    cols: tuple[str, ...] = ()
    if isinstance(item.data, TableResult) and item.data.inline is not None:
        cols = tuple(c.name for c in item.data.columns)
        data = [list(r) for r in item.data.inline]
        rows = Page[list[JsonValue]](**paginate(data, req.page, "row order"))
    return ResultGetOut(
        manifest=man,
        item=ResultItemView(
            item=_item_brief(item),
            summary=item_summary(item),
            series=series,
            arrays=tuple(refs),
            rows=rows,
            columns=cols,
            decimated_from=decimated_from,
        ),
    )


# ---------------------------------------------------------------- export

_MEDIA = {
    "html": "text/html",
    "sarif": "application/sarif+json",
    "touchstone": "text/plain",
    "csv": "text/csv",
    "vtu": "application/xml",
    "json": "application/json",
    "md": "text/markdown",
}
_EXT = {
    "html": "html",
    "sarif": "sarif",
    "touchstone": "s2p",
    "csv": "csv",
    "vtu": "vtu",
    "json": "json",
    "md": "md",
}


def _pick_item(
    bundle: ResultBundle, item_id: str | None, kinds: tuple[str, ...], fmt: str
) -> ResultItem:
    if item_id is not None:
        for i in bundle.items:
            if i.id == item_id:
                if kinds and i.kind not in kinds:
                    raise ServiceError(
                        "USAGE",
                        f"Format {fmt} needs a {'/'.join(kinds)} item, {item_id!r} is {i.kind}.",
                        path="/item_id",
                    )
                return i
        raise ServiceError("NOT_FOUND", f"No item {item_id!r}.", path="/item_id")
    for i in bundle.items:
        if i.kind in kinds:
            return i
    raise ServiceError(
        "NOT_FOUND",
        f"The bundle has no {'/'.join(kinds)} item to export as {fmt}.",
        path="/format",
        hint="Try format=json or md.",
    )


def _html(bundle: ResultBundle) -> str:
    import html

    s = bundle_summary(bundle)
    rows = "".join(
        f"<h2>{html.escape(i.title)} <small>({html.escape(i.kind)})</small></h2>"
        f"<p>{html.escape(i.digest)}</p>"
        for i in s.items
    )
    return (
        "<!doctype html><meta charset=utf-8>"
        f"<title>{html.escape(s.analysis)} {html.escape(s.result_id)}</title>"
        f"<h1>{html.escape(s.analysis)}: {html.escape(s.status)}</h1>"
        f"<p>{html.escape(s.text)}</p>{rows}"
    )


def _md(bundle: ResultBundle) -> str:
    s = bundle_summary(bundle)
    lines = [f"# {s.analysis}: {s.status}", "", s.text, ""]
    for i in s.items:
        lines += [f"## {i.title} ({i.kind})", "", i.digest, ""]
    return "\n".join(lines)


def export_bundle(
    bundle: ResultBundle,
    fmt: ExportFormat,
    dest: Path,
    *,
    item_id: str | None = None,
    overwrite: bool = False,
    board: Any = None,
    artifact_path: Callable[[ArtifactRef], Path] | None = None,
) -> ExportInfo:
    """Write one export to ``dest`` (a file path) and describe it."""
    from oa_results.io import to_csv, to_sarif, to_touchstone, to_vtu

    if dest.exists() and not overwrite:
        raise ServiceError(
            "PERMISSION_DENIED",
            f"{dest} already exists.",
            path="/out_path",
            hint="Pass overwrite=true or choose another out_path.",
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        dest.write_text(bundle.model_dump_json(by_alias=True, indent=1), encoding="utf-8")
    elif fmt == "md":
        dest.write_text(_md(bundle), encoding="utf-8", newline="\n")
    elif fmt == "html":
        dest.write_text(_html(bundle), encoding="utf-8", newline="\n")
    elif fmt == "sarif":
        it = _pick_item(bundle, item_id, ("violations",), fmt)
        to_sarif(it.data, board, dest)  # type: ignore[arg-type]
    elif fmt == "touchstone":
        it = _pick_item(bundle, item_id, ("network",), fmt)
        to_touchstone(it.data, dest)  # type: ignore[arg-type]
    elif fmt == "csv":
        it = _pick_item(bundle, item_id, ("table",), fmt)
        to_csv(it.data, dest, artifact_path)  # type: ignore[arg-type]
    elif fmt == "vtu":
        it = _pick_item(bundle, item_id, ("field",), fmt)
        written = to_vtu(it.data, dest)  # type: ignore[arg-type]
        if written:
            dest = written[0]
    data = dest.read_bytes()
    return ExportInfo(
        path=str(dest),
        media_type=_MEDIA[fmt],
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def export_name(bundle: ResultBundle, fmt: ExportFormat) -> str:
    return f"{bundle.provenance.analysis}.{_EXT[fmt]}"
