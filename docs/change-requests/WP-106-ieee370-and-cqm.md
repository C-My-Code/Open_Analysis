<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->
# WP-106: IEEE 370 metric provenance, CQM definition and open physics items

## Problem

1. TASKS.md asks for PQM/RQM/CQM "as implemented in the IEEE 370 open-source reference code".
   The license of that repository (opensource.ieee.org/elec-char/ieee-370) could not be verified:
   the host returned HTTP 418 to the fetch tool, and research 03 only says "check repo license;
   typically BSD-3". An unverified license cannot be ported into a GPL-3.0-or-later tree.
2. The IEEE 370-2020 text is not in the repository, so the metrics could not be transcribed from
   the standard's equations either.

## What was done

`oa_network.quality` implements PQM, RQM and CQM from first principles in the spirit of the
standard (definitions in the module docstring). No reference code was read or ported. Scales and
thresholds (99.9 good / 99 acceptable, CQM 80) are unchanged from interfaces.md section 4.5.

- PQM = 100 (1 - mean_f max(0, sigma_max - 1 - tol)); RQM from the mean off-diagonal asymmetry.
- CQM is a time-domain pre-arrival measure (share of |h(t)| at t < -2/f_max after DC
  extrapolation and a cosine taper), scaled by `CQM_SCALE = 10`. It is NOT the IEEE 370 CQM and
  its sensitivity is limited: a constant Dk/Df line with Df = 0.02 scores about 95 (pass), while
  Df = 0.2 scores about 50 (warn). The warn fixture therefore uses Df = 0.2.

## Proposed decision (architecture owner)

- Confirm the licensing of the IEEE 370 repository and, if permissive (BSD-3), either allow a
  port with attribution or keep this independent implementation and rename the fields' docs to
  say "IEEE 370 style" (interfaces.md already says "style").
- Accept that `QualityReport.cqm` is implementation-defined until a verified port or the
  standard's equations are available; downstream thresholds (`cqm_min = 80`) were calibrated on
  synthetic lines only.

## Other open items (no spec change needed)

- Cannonball-Huray: the tile area (36 r^2, the footprint of the 3x3 base layer) is a
  placeholder marked `[verify]` against Simonovich DesignCon 2015; it gives K_max near 8.3, which
  is probably too high. Only `Huray` (explicit surface ratio) has a golden test.
- The Huray golden case checks the published equation with typical snowball values, not a
  digitised published curve (no offline source).
- `via_model` is registered through the `open_analysis.services` entry point
  (`oa_physics.via:make_service`); it returns an object with the estimator functions, not a
  Protocol from `oa_framework.services` (none is defined for it yet).
