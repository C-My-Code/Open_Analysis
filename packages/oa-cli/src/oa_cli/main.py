# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``oa`` command line: a thin adapter over the ``oa_service`` catalog (ai-integration.md 9).

Every catalog command becomes ``oa <cli path>`` with options generated from its input model. The
hand-written commands are only ``ingest`` (alias of ``open`` that can write an ``.oab``) and
``serve``. All commands accept ``--json`` (or ``OA_OUTPUT=json``): stdout then carries exactly one
envelope and stderr carries logs and progress. Nothing prompts unless ``--interactive`` is given
and stdin is a TTY. Exit codes: 0 ok, 1 ``--fail-on`` reached, 2 usage/setup/not found/permission,
3 missing tool, 4 internal, 5 quality gate, 6 budget/cancel/timeout, 7 confirmation or stale base.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import sys
import types
import typing
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast, get_args, get_origin

import click
import yaml
from pydantic.fields import FieldInfo

from oa_model._base import OAModel
from oa_service import CallContext, Envelope, ErrorInfo, Registry, envelope_json, exit_code_for
from oa_service.catalog import check_finished
from oa_service.commands import CommandDef
from oa_service.errors import map_exception
from oa_service.models import RunResult
from oa_service.types import oa_version

__all__ = ["build_cli", "main"]

TERMINAL = ("succeeded", "failed", "cancelled")
log = logging.getLogger("oa.cli")

ServiceFactory = Callable[[], Any]


def _default_service() -> Any:
    from oa_service.local import LocalService

    return LocalService()


service_factory: ServiceFactory = _default_service
"""Replaceable in tests: returns the ``OAService`` the CLI talks to."""


# ----------------------------------------------------------------------------- field introspection


def _unwrap(ann: Any) -> Any:
    """Strip ``Annotated`` and ``X | None``."""
    while True:
        origin = get_origin(ann)
        if origin is typing.Annotated:
            ann = get_args(ann)[0]
            continue
        if origin in (typing.Union, types.UnionType):
            rest = [a for a in get_args(ann) if a is not type(None)]
            if len(rest) == 1:
                ann = rest[0]
                continue
        return ann


def _kind(field: FieldInfo) -> tuple[str, Any]:
    """('bool'|'choice'|'scalar'|'json', extra) for a model field."""
    ann = _unwrap(field.annotation)
    if ann is bool:
        return "bool", None
    if get_origin(ann) is Literal:
        return "choice", [str(a) for a in get_args(ann)]
    if ann in (str, int, float):
        return "scalar", None
    return "json", None


def _is_model(field: FieldInfo, cls: type[OAModel]) -> bool:
    ann = _unwrap(field.annotation)
    return isinstance(ann, type) and issubclass(ann, cls)


def _load_json(value: str) -> Any:
    """JSON text, or ``@file`` (JSON or YAML)."""
    if value.startswith("@"):
        text = Path(value[1:]).read_text(encoding="utf-8")
        return yaml.safe_load(text)
    return json.loads(value)


# ----------------------------------------------------------------------------- project resolution


def find_project_here() -> Path | None:
    for d in (Path.cwd(), *Path.cwd().parents):
        for n in ("oa-project.yaml", "oa-project.yml"):
            if (d / n).is_file():
                return d / n
    return None


def _looks_like_id(v: str) -> bool:
    return v.startswith("p_") and len(v) == 14 and not Path(v).exists()


# ----------------------------------------------------------------------------- output


def _emit(env: Envelope[Any], as_json: bool) -> None:
    if as_json:
        sys.stdout.write(envelope_json(env) + "\n")
        sys.stdout.flush()
        return
    for w in env.warnings:
        click.echo(f"warning[{w.code}]: {w.message}", err=True)
    if not env.ok:
        for e in env.errors:
            where = f" at {e.path}" if e.path else ""
            click.echo(f"error[{e.code}]: {e.message}{where}", err=True)
            if e.hint:
                click.echo(f"  hint: {e.hint}", err=True)
        return
    doc = env.model_dump(mode="json", by_alias=True)["data"]
    sys.stdout.write(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100))
    sys.stdout.flush()


def _error_env(*errors: ErrorInfo, schema: str = "oa-error/1") -> Envelope[Any]:
    return Envelope[Any](
        schema=schema,
        version=oa_version(),
        ok=False,
        data=None,
        warnings=(),
        errors=tuple(sorted(errors, key=lambda e: (e.code, e.path or ""))),
    )


def _count(v: object) -> int:
    return int(v) if isinstance(v, int | float) else 0


def _fail_on_reached(env: Envelope[Any], level: str) -> bool:
    if level == "never" or not isinstance(env.data, RunResult) or env.data.job is None:
        return False
    s = env.data.job.summary
    if s is None:
        return False
    err = warn = 0
    for item in s.items:
        counts = item.stats.get("counts") if item.kind == "violations" else None
        if isinstance(counts, dict):
            err += _count(counts.get("error"))
            warn += _count(counts.get("warning"))
    return err > 0 or (level == "warning" and warn > 0)


# ----------------------------------------------------------------------------- command execution


class _Run:
    """One CLI invocation of a catalog command."""

    def __init__(self, d: CommandDef, opts: dict[str, Any]) -> None:
        self.d = d
        self.opts = opts
        self.as_json = bool(opts.get("json_out")) or os.environ.get("OA_OUTPUT") == "json"

    def ctx(self) -> CallContext:
        return CallContext(caller="cli", allow_writes=True)

    def resolve_project(self, reg: Registry, value: str | None) -> str:
        ctx = self.ctx()
        if value is None:
            here = find_project_here()
            if here is None:
                raise _Usage(
                    "No project given and no oa-project.yaml found here or above.",
                    "/project_id",
                    "Pass a project directory or board file (--project), or run in a project.",
                )
            value = str(here)
        if _looks_like_id(value):
            return value
        env = reg.dispatch("board.open", {"path": value}, ctx)
        if not env.ok:
            raise _Envelope(env)
        assert env.data is not None
        return str(env.data.project_id)

    def build_args(self, reg: Registry) -> dict[str, Any]:
        d, o = self.d, self.opts
        args: dict[str, Any] = {}
        if o.get("args"):
            args.update(_load_json(o["args"]))
        for name, field in d.input_type.model_fields.items():
            if name == "project_id":
                continue
            if name == "page":
                page = {k: o[k] for k in ("offset", "limit") if o.get(k) is not None}
                if page:
                    args["page"] = page
                continue
            if name == "budget":
                b = {
                    k: o[k]
                    for k in ("max_runtime_s", "max_memory_gb", "max_cpus")
                    if o.get(k) is not None
                }
                if o.get("no_external"):
                    b["allow_external"] = False
                if b:
                    args["budget"] = b
                continue
            if name == "wait_s" and d.spec.long_running:
                continue
            key = "confirm" if name == "confirm" else name
            val = o.get(key)
            if val is None:
                continue
            kind, _ = _kind(field)
            if kind == "json" and isinstance(val, str):
                val = _load_json(val)
            if kind == "bool" and not val and field.default is False:
                continue  # unset flag: keep the model default
            args[name] = val
        if "project_id" in d.input_type.model_fields and "project_id" not in args:
            args["project_id"] = self.resolve_project(reg, o.get("project") or o.get("project_arg"))
        elif "project_id" in args:
            args["project_id"] = self.resolve_project(reg, str(args["project_id"]))
        return args

    def execute(self) -> int:
        d, o = self.d, self.opts
        svc = service_factory()
        try:
            reg = Registry.load(svc)
            try:
                args = self.build_args(reg)
            except _Envelope as e:
                return self.finish(e.env)
            except _Usage as e:
                return self.finish(_error_env(e.info(), schema=d.spec.output_model))
            wait = d.spec.long_running and o.get("wait", True)
            if d.spec.long_running:
                if wait and o.get("progress") != "json":
                    args["wait_s"] = float(o.get("timeout") or 600)
                else:
                    args["wait_s"] = 0.0
            env = reg.dispatch(d.spec.name, args, self.ctx())
            env = self.confirm_retry(reg, args, env)
            if env.ok and d.spec.long_running and wait and isinstance(env.data, RunResult):
                env = self.after_run(svc, env, float(o.get("timeout") or 600))
            if env.ok and o.get("sarif") and isinstance(env.data, RunResult) and env.data.job:
                self.write_sarif(reg, env)
            return self.finish(env)
        finally:
            closer = getattr(svc, "close", None)
            if closer is not None:
                closer()

    def confirm_retry(
        self, reg: Registry, args: dict[str, Any], env: Envelope[Any]
    ) -> Envelope[Any]:
        """With --interactive on a TTY, ask once; an EOF or refusal keeps the exit-7 envelope."""
        if env.ok or env.errors[0].code != "CONFIRMATION_REQUIRED":
            return env
        if not (self.opts.get("interactive") and sys.stdin.isatty()):
            return env
        try:
            agreed = click.confirm(env.errors[0].message + " Proceed?", err=True)
        except click.Abort:  # a closed stdin can look like a TTY on Windows (NUL)
            return env
        if not agreed:
            return env
        return reg.dispatch(self.d.spec.name, {**args, "confirm": True}, self.ctx())

    def after_run(self, svc: Any, env: Envelope[Any], timeout: float) -> Envelope[Any]:
        """Progress streaming, timeout and finished-job checks for run commands."""
        data = env.data
        assert data is not None
        job = data.job
        if job is None:
            return env
        if self.opts.get("progress") == "json":
            import time

            seq, deadline = 0, time.monotonic() + timeout
            while time.monotonic() < deadline:
                for ev in svc.events(job.job_id, seq, 200):
                    seq = ev.seq
                    click.echo(
                        json.dumps({"job_id": job.job_id, **ev.model_dump(mode="json")}), err=True
                    )
                if svc.job_handle(job.job_id).state in TERMINAL:
                    for ev in svc.events(job.job_id, seq, 1000):
                        click.echo(
                            json.dumps({"job_id": job.job_id, **ev.model_dump(mode="json")}),
                            err=True,
                        )
                    break
                time.sleep(0.05)
            job = svc.job_handle(job.job_id)
            data = data.model_copy(update={"job": job})
            env = env.model_copy(update={"data": data})
        if job.state not in TERMINAL:
            return _error_env(
                ErrorInfo(
                    code="TIMEOUT",
                    message=f"Job {job.job_id} is still {job.state} after {timeout:g} s.",
                    hint="Poll with `oa jobs status JOB`, or wait longer with --timeout.",
                    details={"job_id": job.job_id},
                ),
                schema=self.d.spec.output_model,
            )
        try:
            check_finished(svc, job.state, job)
        except Exception as exc:
            return _error_env(*map_exception(exc), schema=self.d.spec.output_model)
        return env

    def write_sarif(self, reg: Registry, env: Envelope[Any]) -> None:
        assert env.data is not None and env.data.job is not None
        rid = env.data.job.result_id
        if rid:
            out = reg.dispatch(
                "results.export",
                {
                    "result_id": rid,
                    "format": "sarif",
                    "out_path": self.opts["sarif"],
                    "overwrite": True,
                },
                self.ctx(),
            )
            if not out.ok:
                click.echo(f"warning: SARIF export failed: {out.errors[0].message}", err=True)

    def finish(self, env: Envelope[Any]) -> int:
        _emit(env, self.as_json)
        if not env.ok:
            return exit_code_for(env.errors[0].code)
        return 1 if _fail_on_reached(env, str(self.opts.get("fail_on") or "never")) else 0


class _Usage(Exception):
    def __init__(self, message: str, path: str, hint: str) -> None:
        super().__init__(message)
        self.message, self.path, self.hint = message, path, hint

    def info(self) -> ErrorInfo:
        return ErrorInfo(code="USAGE", message=self.message, path=self.path, hint=self.hint)


class _Envelope(Exception):
    def __init__(self, env: Envelope[Any]) -> None:
        super().__init__("envelope")
        self.env = env


# ----------------------------------------------------------------------------- click generation


def _opt(name: str) -> str:
    return "--" + name.replace("_", "-")


def _command_for(d: CommandDef) -> click.Command:
    spec = d.spec
    params: list[click.Parameter] = []
    fields = d.input_type.model_fields
    only_project = d.cli_args == ("project_id",)
    for a in d.cli_args:
        if a == "project_id":
            continue
        params.append(click.Argument([a], required=False))  # pydantic reports what is missing
    if only_project:
        params.append(click.Argument(["project_arg"], required=False))
    if "project_id" in fields:
        params.append(
            click.Option(["--project", "-p"], help="Project directory, board file or id.")
        )
    for name, field in fields.items():
        if name in d.cli_args or name == "project_id":
            continue
        desc = field.description or ""
        if name == "page":
            params.append(click.Option(["--offset"], type=int, help="First item to return."))
            params.append(click.Option(["--limit"], type=int, help="Maximum items to return."))
            continue
        if name == "budget":
            params += [
                click.Option(["--max-runtime", "max_runtime_s"], help="Wall-clock limit [s]."),
                click.Option(["--max-memory", "max_memory_gb"], help="Peak memory limit [GB]."),
                click.Option(["--max-cpus"], type=int, help="Maximum concurrent workers."),
                click.Option(
                    ["--no-external", "no_external"], is_flag=True, help="Forbid solvers."
                ),
            ]
            continue
        if name == "wait_s" and spec.long_running:
            continue
        kind, extra = _kind(field)
        if name == "confirm":
            params.append(
                click.Option(["--yes", "confirm"], is_flag=True, help="Confirm the write.")
            )
        elif kind == "bool":
            params.append(click.Option([_opt(name)], is_flag=True, default=False, help=desc))
        elif kind == "choice":
            params.append(click.Option([_opt(name)], type=click.Choice(extra), help=desc))
        elif kind == "scalar":
            params.append(click.Option([_opt(name)], help=desc))
        else:
            params.append(
                click.Option([_opt(name)], metavar="JSON|@FILE", help=f"{desc} (JSON or @file)")
            )
    params.append(click.Option(["--args"], metavar="JSON|@FILE", help="Whole input as JSON."))
    if spec.long_running:
        params += [
            click.Option(["--wait/--no-wait"], default=True, help="Wait for the job (default)."),
            click.Option(["--timeout"], type=float, default=600.0, help="Wait limit [s]."),
            click.Option(["--progress"], type=click.Choice(["none", "json"]), default="none"),
            click.Option(
                ["--fail-on"],
                type=click.Choice(["error", "warning", "never"]),
                default="error" if spec.name == "erc.run" else "never",
                help="Exit 1 when violations of this severity exist.",
            ),
        ]
        if spec.name == "erc.run":
            params.append(click.Option(["--sarif"], type=click.Path(), help="Also write SARIF."))
    if spec.name == "meta.schema":
        params.append(click.Option(["--list", "list_"], is_flag=True, help="List schema ids."))
    params.append(
        click.Option(["--json", "json_out"], is_flag=True, help="One JSON envelope on stdout.")
    )
    params.append(
        click.Option(["--interactive"], is_flag=True, help="Ask before writes (TTY only).")
    )

    def callback(**kw: Any) -> None:
        kw.pop("list_", None)
        code = _Run(d, kw).execute()
        raise SystemExit(code)

    return click.Command(spec.cli[-1], params=params, callback=callback, help=spec.description)


def build_cli() -> click.Group:
    """The root click group: hand-written commands plus one command per catalog entry."""
    root = root_group
    groups: dict[tuple[str, ...], click.Group] = {(): root}
    probe = Registry.load(cast(Any, _NullService()))
    for d in probe.definitions():
        path = d.spec.cli
        if (path,) and path[0] in root.commands and len(path) == 1:
            continue  # hand-written wins
        parent = root
        for i, part in enumerate(path[:-1]):
            key = path[: i + 1]
            if key not in groups:
                g = click.Group(part, help=f"{part} commands")
                parent.add_command(g)
                groups[key] = g
            parent = groups[key]
        parent.add_command(_command_for(d))
    return root


class _NullService:
    """Placeholder so the catalog can be enumerated without constructing a real service."""


# ----------------------------------------------------------------------------- hand-written


@click.group(
    name="oa",
    help="Open_Analysis: PCB signal, power and RF integrity. Use --json for machine output.",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(oa_version(), prog_name="oa")
def root_group() -> None:
    """Root group; commands are generated from the catalog."""


@root_group.command("ingest")
@click.argument("board", type=click.Path(path_type=Path))
@click.option("--out", type=click.Path(path_type=Path), help="Write the neutral board (.oab.json).")
@click.option("--refill-zones", is_flag=True, help="Refill KiCad zones first.")
@click.option("--format", "format_", default="summary", help="json (same as --json) or summary.")
@click.option("--json", "json_out", is_flag=True, help="One JSON envelope on stdout.")
def ingest(board: Path, out: Path | None, refill_zones: bool, format_: str, json_out: bool) -> None:
    """Read a board; optionally write it as a neutral .oab.json (alias of `oa open`)."""
    from oa_model.io import dump_board

    as_json = json_out or format_ == "json" or os.environ.get("OA_OUTPUT") == "json"
    svc = service_factory()
    try:
        reg = Registry.load(svc)
        args = {"path": str(board), "refill_zones": "if_unfilled" if refill_zones else "never"}
        env = reg.dispatch("board.open", args, CallContext(caller="cli", allow_writes=True))
        if env.ok and out is not None and env.data is not None:
            dump_board(svc.project(env.data.project_id).board, out)
        _emit(env, as_json)
        raise SystemExit(0 if env.ok else exit_code_for(env.errors[0].code))
    finally:
        closer = getattr(svc, "close", None)
        if closer is not None:
            closer()


@root_group.command("serve")
@click.argument("project", required=False, type=click.Path(path_type=Path))
@click.option("--port", default=0, help="Port (0 = free port).")
@click.option("--host", default="127.0.0.1", help="Bind address (non-loopback warns).")
@click.option("--no-browser", is_flag=True, help="Do not open a browser.")
def serve(project: Path | None, port: int, host: str, no_browser: bool) -> None:
    """Start the local REST server and web UI (needs oa-server)."""
    try:
        run = importlib.import_module("oa_server.run")
    except ModuleNotFoundError:
        click.echo("oa-server is not installed: pip install oa-cli[serve]", err=True)
        raise SystemExit(3) from None
    raise SystemExit(run.serve(project, host=host, port=port, open_browser=not no_browser))


# ----------------------------------------------------------------------------- entry point


def main(argv: list[str] | None = None) -> None:
    """Console script ``oa``. Exits with the documented exit code."""
    logging.basicConfig(
        stream=sys.stderr, level=logging.WARNING, format="%(levelname)s %(message)s"
    )
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args or os.environ.get("OA_OUTPUT") == "json"
    try:
        cli = build_cli()
        cli.main(args=args, prog_name="oa", standalone_mode=False)
    except click.exceptions.Exit as e:
        raise SystemExit(e.exit_code) from None
    except click.exceptions.Abort:
        raise SystemExit(130) from None
    except click.ClickException as e:
        info = ErrorInfo(code="USAGE", message=e.format_message(), hint="Run `oa --help`.")
        if as_json:
            _emit(_error_env(info), True)
        else:
            e.show()
        raise SystemExit(2) from None
    except SystemExit:
        raise
    except Exception as exc:
        infos = map_exception(exc)
        _emit(_error_env(*infos), as_json)
        raise SystemExit(exit_code_for(infos[0].code)) from None
    raise SystemExit(0)


if __name__ == "__main__":  # pragma: no cover
    main()
