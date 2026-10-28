<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->
# WP-204: `2d_sheet` accuracy class is not expressible in `oa_results`

## Problem

ai-integration.md section 4.1 requires `ResultItem.accuracy: Accuracy` with
`accuracy_class="2d_sheet"` for `pi.dc`. The merged `oa_results` has no `oa_results.accuracy`
module and no `ResultItem.accuracy`; `ResultContainer.accuracy_class` only allows
`reference | validated | screening | estimate | unverified`.

## Local adapter

Every `pi.dc` payload sets `accuracy_class="validated"` and one `ValidityFlag`
`code="accuracy.2d_sheet"` (method, tolerance). The bundle has an info diagnostic `PI.ACCURACY`
with the tolerance basis and assumptions. If `oa_results.accuracy.Accuracy` and
`ResultItem.accuracy` appear, `oa_pi.dc.analysis._accuracy_extras` fills them without code change.

## Proposed change

Land A4 (`Accuracy`, `AccuracyClass`, `ResultItem.accuracy`) in `oa_results`; then drop the flag.
