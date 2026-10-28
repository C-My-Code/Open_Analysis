# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuration and task payload of the ``em.sparams`` analysis (interfaces.md section 11)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from oa_model._base import OAModel
from oa_model.common import Coord, LayerId, Length, NetId
from oa_model.em import Convergence, FrequencyPlan, MeshSettings
from oa_model.setup.ports import Port

__all__ = ["BoundaryConfig", "EmSparamsConfig", "RegionConfig", "TaskPayload"]


class RegionConfig(OAModel):
    nets: tuple[NetId, ...] = Field(description="Signal nets of interest (at least one).")
    reference_nets: tuple[NetId, ...] = Field(
        default=(), description="Reference nets; empty = automatic from the ReferenceMap."
    )
    bbox: tuple[Coord, Coord, Coord, Coord] | None = Field(
        default=None,
        description="xmin, ymin, xmax, ymax of the region [m]; None = automatic (net extent "
        "plus margin; trace ends are then extended to the region faces for the ports).",
    )
    margin: Length | None = Field(
        default=None,
        description="Margin around the automatic bbox [m]; None = max(5*h_max, 3*w_max, "
        "lambda_min/10).",
    )
    layers: tuple[LayerId, ...] | None = Field(
        default=None, description="Restrict the z range to these layers; None = full stack."
    )
    keep_all_copper: bool = Field(
        default=True, description="Keep every copper polygon in the bbox, not only the nets."
    )


class BoundaryConfig(OAModel):
    mode: Literal["auto", "closed", "open"] = Field(
        default="auto",
        description="'closed': PEC side walls (stripline-like SI cavities); 'open': absorbing "
        "side walls with air padding; 'auto': closed when every signal conductor lies between "
        "reference planes.",
    )
    air_padding: Length | None = Field(
        default=None,
        description="Air around the region on open faces [m]; None = max(lambda0/10, 5 x stack "
        "height), at most lambda0/4, at f_max.",
    )


class EmSparamsConfig(OAModel):
    solver: str = Field(default="openems", description="Name of the EM solver adapter.")
    region: RegionConfig = Field(description="Region of the board to extract.")
    frequency: FrequencyPlan = Field(description="Frequency plan [Hz].")
    mesh: MeshSettings = Field(default=MeshSettings(), description="Mesh settings.")
    convergence: Convergence = Field(
        default=Convergence(energy_decay_db=-40.0, max_timesteps=300_000, delta_s=0.05),
        description="Convergence criteria (energy decay [dB], delta S). The default end "
        "criterion is -40 dB with a 300k time-step cap; the mesh-refinement limit is max |dS| 0.05 "
        "(about 0.4 dB on a thru, FDTD mesh changes move S21 by 0.02-0.03).",
    )
    boundary: BoundaryConfig = Field(default=BoundaryConfig(), description="Boundary selection.")
    ports: tuple[Port, ...] = Field(
        default=(),
        description="Ports (trace_end, lumped, coax). Empty = one trace_end port per region "
        "face crossed by the first signal net(s). The job layer does not yet copy "
        "AnalysisSetup.ports into the config, so put them here.",
    )
    raise_fmin: bool = Field(
        default=True,
        description="Start the sweep at the lowest frequency the solver excitation covers "
        "(openEMS: 0.1 x f_max) instead of the requested f_min.",
    )
    fix_quality: bool = Field(
        default=False,
        description="Let the quality gate repair the network (DC point, vector-fit passivity "
        "enforcement). Fixes are recorded in the QualityReport.",
    )
    refinement_check: bool = Field(
        default=False,
        description="Also solve with a finer mesh and compare the S-parameters "
        "(max |dS| <= convergence.delta_s). Costs about 3-5 times the base run.",
    )
    refinement_factor: float = Field(
        default=1.4, gt=1.0, description="Cells-per-wavelength factor of the refinement run [1]."
    )
    energy_margin_db: float = Field(
        default=3.0,
        ge=0,
        description="Tolerance on the FDTD end criterion: the final energy may exceed "
        "convergence.energy_decay_db by this much and still count as converged [dB].",
    )
    z0_tolerance: float = Field(
        default=0.10,
        gt=0,
        description="Relative difference between the port impedance and the closed-form line "
        "impedance above which a warning is issued [1].",
    )
    estimate_scale: float = Field(
        default=1.0,
        gt=0,
        description="Multiplier on the adapter run-time estimate (calibration) [1].",
    )
    adapter_options: dict[str, JsonValue] = Field(
        default={}, description="Extra MeshSettings.expert keys, namespaced by adapter."
    )


class TaskPayload(OAModel):
    role: Literal["prepare", "run"] = Field(description="Task role.")
    config: dict[str, JsonValue] = Field(description="EmSparamsConfig as plain JSON.")
    variant: Literal["base", "fine"] = Field(default="base", description="Mesh variant.")
    index: int = Field(default=0, ge=0, description="Command (excitation) index for 'run'.")
