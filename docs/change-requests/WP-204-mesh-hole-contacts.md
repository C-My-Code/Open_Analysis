<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->
# WP-204: `oa_mesh` hole-mode contacts return wrong ring nodes

## Problem

`mesh_copper_sheets` with `Contact(mode="hole", radius=1e-4, segments=16)` returns
`SheetMesh.contacts[i].nodes` such as `[0 1 1 1 1 2 2 2 2 3 3 3 3 4 4 4]` (16 entries, 5 distinct
nodes) with `radial_error` of 1e-2 .. 3e-3 m, whereas `mode="embedded"` returns 16 distinct
nodes with `radial_error` about 1e-18. Reproduced on a 40 x 30 mm rectangle with one contact
at (0.0436, -0.0265).

## Local adapter

`oa_pi` uses `mode="embedded"` for via barrel rings (the ring nodes are merged into one unknown,
so the meshed disc inside the ring has no effect on the result).

## Proposed change

Fix the ring-node lookup for hole-mode contacts in `oa_mesh.sheets` and add a test that checks
`radial_error` for hole contacts.
