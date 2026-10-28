# SPDX-License-Identifier: GPL-3.0-or-later
"""Project manifest and analysis setup files (interfaces.md section 6)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, JsonValue

from oa_model._base import OAModel
from oa_model.setup.ports import Port


class ReadOptions(OAModel):
    project_file: Path | None = Field(
        default=None, description="'.kicad_pro' next to the board; auto-detected if None."
    )
    refill_zones: Literal["never", "if_unfilled", "always"] = Field(
        default="never", description="Refill zones through oa_kicad_bridge."
    )
    arc_tolerance: float = Field(default=1e-6, gt=0, description="Max arc sagitta error, metres.")
    strict: bool = Field(default=False, description="Treat reader warnings as errors.")


class Waiver(OAModel):
    fingerprint: str | None = Field(default=None, description="Exact violation fingerprint.")
    rule_id: str | None = Field(default=None, description="Rule id, optionally with `nets`.")
    nets: tuple[str, ...] = Field(default=(), description="Net name globs.")
    reason: str = Field(description="Why this is waived (required).")
    author: str | None = Field(default=None, description="Who waived it.")
    expires: str | None = Field(default=None, description="ISO date after which it lapses.")


class ProjectManifest(OAModel):
    schema: Literal["oa-project/1"] = Field(description="Schema tag.")  # type: ignore[assignment]
    name: str = Field(description="Project name.")
    board: Path = Field(
        description="Relative path to the board: .kicad_pcb, .xml, .PcbDoc, .oab or .oab.json."
    )
    physics: Path | None = Field(default=None, description="Physics sidecar path.")
    intent: Path | None = Field(default=None, description="Design intent path.")
    setups_dir: Path = Field(default=Path("oa/setups"), description="Directory of setup files.")
    read_options: ReadOptions = Field(default=ReadOptions(), description="Reader options.")
    tools: dict[str, dict[str, str]] = Field(
        default={}, description="Per-project external tool overrides."
    )


class AnalysisSetup(OAModel):
    schema: Literal["oa-setup/1"] = Field(description="Schema tag.")  # type: ignore[assignment]
    name: str = Field(description="Unique in the project; defaults to the file stem.")
    analysis: str = Field(description="Analysis kind, e.g. 'em.sparams'.")
    description: str = Field(default="", description="Free text.")
    config: dict[str, JsonValue] = Field(
        description="Analysis-specific configuration, validated against Analysis.Config at load."
    )
    ports: tuple[Port, ...] = Field(default=(), description="Ports for EM / PI analyses.")
    waivers: tuple[Waiver, ...] = Field(default=(), description="ERC waivers.")


def load_project(path: str | Path) -> ProjectManifest:
    """Load an `oa-project.yaml`."""
    return ProjectManifest.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def load_setup(path: str | Path) -> AnalysisSetup:
    """Load a setup YAML; `name` defaults to the file stem."""
    p = Path(path)
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "name" not in data:
        data["name"] = p.stem
    return AnalysisSetup.model_validate(data)
