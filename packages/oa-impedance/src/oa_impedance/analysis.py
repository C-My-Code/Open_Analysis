# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``impedance.xsection`` analysis: one cross-section -> RLGC, Z0/Zdiff, eps_eff."""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import ClassVar, Literal

from pydantic import Field

from oa_framework.analysis import Plan, PlanContext, RunContext, Task, TaskOutput
from oa_impedance.service import SOLVER_VERSION, ImpedanceServiceImpl
from oa_model._base import OAModel
from oa_model.common import Diagnostic
from oa_model.xsection import CrossSection
from oa_results.bundle import Provenance, ResultBundle, ResultItem
from oa_results.container import ValidityFlag
from oa_results.rlgc import ImpedanceResult, RLGCResultSet
from oa_results.scalars import Scalar, ScalarSet

__all__ = ["ImpedanceXsection", "XsectionConfig", "XsectionPayload"]

KIND = "impedance.xsection"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
TOLERANCE_BASIS = {
    "bem": "golden:impedance_bem (thin-strip exact cases <= 0.3 %, BEM vs HJ <= 1 %)",
    "closed_form": "golden:impedance_closedform (formula lock 1e-6; published range checked)",
}


class XsectionConfig(OAModel):
    xsection: CrossSection | None = Field(
        default=None, description="Inline cross-section to analyse (CrossSection, dimensionless)."
    )
    sample_ref: str | None = Field(
        default=None,
        description="Reference to a geometry sample (WP-104); not yet supported, use xsection.",
    )
    freqs: tuple[float, ...] = Field(
        default=(0.0,), description="Frequencies [Hz], ascending; 0 = static (lossless)."
    )
    method: Literal["auto", "closed_form", "bem", "fem"] = Field(
        default="auto", description="Solver: auto picks a closed form when one applies else BEM."
    )


class XsectionPayload(OAModel):
    xsection: CrossSection = Field(description="Cross-section to solve (CrossSection).")
    freqs: tuple[float, ...] = Field(description="Frequencies [Hz], ascending.")
    method: Literal["auto", "closed_form", "bem", "fem"] = Field(description="Solver selection.")


def _ulid() -> str:
    n = int(time.time() * 1000)
    ts = ""
    for _ in range(10):
        ts = _CROCKFORD[n % 32] + ts
        n //= 32
    rnd = int.from_bytes(os.urandom(10), "big")
    body = ""
    for _ in range(16):
        body += _CROCKFORD[rnd % 32]
        rnd //= 32
    return ts + body


def _service(ctx: PlanContext | RunContext) -> ImpedanceServiceImpl:
    svc = ctx.services()
    if svc.has("impedance"):
        return svc.get("impedance")  # type: ignore[no-any-return]
    return _LOCAL


_LOCAL = ImpedanceServiceImpl()


class ImpedanceXsection:
    kind: ClassVar[str] = KIND
    version: ClassVar[str] = "1"
    title: ClassVar[str] = "Cross-section impedance (closed form / 2D BEM)"
    Config: ClassVar[type[OAModel]] = XsectionConfig
    TaskPayload: ClassVar[type[OAModel]] = XsectionPayload

    def validate_setup(self, ctx: PlanContext, cfg: OAModel) -> tuple[Diagnostic, ...]:
        assert isinstance(cfg, XsectionConfig)
        out: list[Diagnostic] = []
        if cfg.xsection is None:
            out.append(
                Diagnostic(
                    severity="error",
                    code="IMPEDANCE.NO_XSECTION",
                    message="config.xsection is missing"
                    + (
                        " (sample_ref needs oa_geometry, not available yet)"
                        if cfg.sample_ref
                        else ""
                    ),
                    path="/config/xsection",
                    hint="Provide an inline CrossSection in config.xsection.",
                )
            )
        if list(cfg.freqs) != sorted(cfg.freqs) or any(f < 0 for f in cfg.freqs):
            out.append(
                Diagnostic(
                    severity="error",
                    code="IMPEDANCE.BAD_FREQS",
                    message="freqs must be ascending and >= 0",
                    path="/config/freqs",
                    hint="Sort the frequency list and remove negative values.",
                )
            )
        return tuple(out)

    def plan(self, ctx: PlanContext, cfg: OAModel) -> Plan:
        assert isinstance(cfg, XsectionConfig) and cfg.xsection is not None
        payload = XsectionPayload(xsection=cfg.xsection, freqs=cfg.freqs, method=cfg.method)
        return Plan(
            tasks=(
                Task(
                    id=f"xs:{cfg.xsection.key()}",
                    analysis=KIND,
                    payload=payload.model_dump(mode="json"),
                ),
            )
        )

    def run_task(self, ctx: RunContext, payload: OAModel) -> TaskOutput:
        assert isinstance(payload, XsectionPayload)
        res = _service(ctx).solve(payload.xsection, payload.freqs, method=payload.method)
        return TaskOutput(
            task_id=ctx.task.id, data={"result": res.model_dump(mode="json", by_alias=True)}
        )

    def reduce(
        self, ctx: RunContext, cfg: OAModel, outputs: Mapping[str, TaskOutput]
    ) -> ResultBundle:
        assert isinstance(cfg, XsectionConfig)
        t0 = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        results = [
            ImpedanceResult.model_validate(o.data["result"]) for _, o in sorted(outputs.items())
        ]
        res = results[0]
        is_bem = res.method != "closed_form"
        cls: Literal["validated", "screening"] = "validated" if is_bem else "screening"
        basis = TOLERANCE_BASIS["bem" if is_bem else "closed_form"]
        accuracy = "2d_field" if is_bem else "closed_form"
        flags = (
            ValidityFlag(
                code="accuracy",
                ok=res.validity == "in_range",
                message=f"accuracy_class={accuracy}; method={res.model_name}; "
                f"validity={res.validity}; tolerance_basis={basis}",
            ),
            *res.validity_flags,
        )
        rset = RLGCResultSet(results=tuple(results), accuracy_class=cls, validity_flags=flags)
        p = res.params
        scalars: list[Scalar] = []
        f0 = float(p.freqs.array[0])
        for name, unit in (
            ("z0", "ohm"),
            ("z_diff", "ohm"),
            ("z_comm", "ohm"),
            ("z_odd", "ohm"),
            ("z_even", "ohm"),
        ):
            arr = getattr(p, name)
            if arr is not None:
                scalars.append(
                    Scalar(
                        name=f"{name}@{f0:g}Hz",
                        value=float(arr.array[0]),
                        unit=unit,
                        status="pass" if res.validity == "in_range" else "warn",
                    )
                )
        eps = p.eps_eff_modal.array
        scalars.append(Scalar(name=f"eps_eff_mode0@{f0:g}Hz", value=float(eps[0, 0]), unit=""))
        scalars.append(
            Scalar(name=f"delay_mode0@{f0:g}Hz", value=float(p.delay_per_m.array[0, 0]), unit="s/m")
        )
        sset = ScalarSet(values=tuple(scalars), accuracy_class=cls, validity_flags=flags)
        diags: list[Diagnostic] = []
        if res.validity == "invalid":
            diags.append(
                Diagnostic(
                    severity="warning",
                    code="RESULT.INVALID_INPUT_RANGE",
                    message="closed form evaluated far outside its published range: "
                    + "; ".join(n for n in res.notes if n.startswith("validity:")),
                    hint="Re-run with method='bem'.",
                )
            )
        elif res.validity == "extrapolated":
            diags.append(
                Diagnostic(
                    severity="warning",
                    code="IMPEDANCE.EXTRAPOLATED",
                    message="result is outside the validated range of the method: "
                    + "; ".join(
                        n for n in res.notes if "validity:" in n or "quasi-static model" in n
                    ),
                    hint="Use method='bem', or reduce the maximum frequency.",
                )
            )
        headline = ", ".join(f"{s.name}={s.value:.4g} ohm" for s in scalars if s.unit == "ohm")
        summary = (
            f"{res.method} impedance ({res.model_name}), {res.validity}: {headline or 'n/a'}; "
            f"accuracy {accuracy}."
        )
        board_hash = ""
        try:
            board_hash = ctx.board().content_hash()
        except Exception:  # inline cross-section runs may have no board
            board_hash = ""
        cfg_json = cfg.model_dump_json()
        return ResultBundle(
            id=_ulid(),
            status="ok" if res.validity == "in_range" else "warning",
            summary=summary,
            items=(
                ResultItem(id="rlgc", title="Impedance / RLGC", kind="rlgc", data=rset),
                ResultItem(id="scalars", title="Headline values", kind="scalars", data=sset),
            ),
            diagnostics=tuple(diags),
            provenance=Provenance(
                analysis=KIND,
                analysis_version=self.version,
                board_hash=board_hash,
                physics_hash=None,
                setup_hash="sha256:" + hashlib.sha256(cfg_json.encode()).hexdigest(),
                config=cfg.model_dump(mode="json"),
                solver=res.model_name,
                solver_version=SOLVER_VERSION,
                started_utc=t0,
                finished_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                inputs_hash="sha256:"
                + hashlib.sha256((res.xs_key + cfg_json).encode()).hexdigest(),
            ),
        )
