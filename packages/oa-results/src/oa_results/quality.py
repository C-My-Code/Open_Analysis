# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""S-parameter quality report types (interfaces.md section 4.5).

The *types* live here because ``NetworkResult.quality`` is required and ``oa_results`` (L1)
cannot import ``oa_network`` (L2). ``oa_network.quality`` re-exports them and implements
``assess`` / ``gate``.
"""

from __future__ import annotations

from typing import Literal

from oa_model._base import OAModel
from oa_model.common import Frequency


class QualityCriteria(OAModel):
    passivity_tol: float = 1e-6  # max singular value <= 1 + tol counts as passive
    pqm_min: float = 99.0  # IEEE 370 style: percent
    rqm_min: float = 99.0
    cqm_min: float = 80.0  # causality metric threshold for "acceptable"
    require_dc: Literal["present", "extrapolatable", "none"] = "extrapolatable"
    max_df_fraction: float = 0.01  # frequency step <= 1% of f_max for causality evaluation


class QualityReport(OAModel):
    verdict: Literal["pass", "warn", "fail"]
    pqm: float
    rqm: float
    cqm: float  # percent, IEEE 370-2020 style definitions
    max_singular_value: float
    worst_passivity_freq: Frequency | None
    reciprocity_max_err: float
    dc: Literal["present", "extrapolated", "missing"]
    f_min: float
    f_max: float
    n_freqs: int
    criteria: QualityCriteria
    fixes_applied: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
