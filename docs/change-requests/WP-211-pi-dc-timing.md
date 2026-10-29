<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->
# WP-211: restore the WP-204 timing criterion (total < 20 s for 3e5 unknowns)

## Problem

WP-204 relaxed `test_three_hundred_thousand_unknowns_solve_under_20_s` (packages/oa-pi) to
assemble+solve < 20 s and total < 60 s, because gmsh meshing took 12-20 s under load.

## Measurement (WP-211, gmsh 4.15.2, Windows, 2 threads, idle machine)

283124 unknowns: total 10.5 s = mesh 8.8 s + assemble 0.2 s + solve 1.4 s, peak 0.95 GB.
The mesh is one 100x100 mm piece at uniform h, so neither the process pool, interior coarsening
nor `algorithm="delaunay"` (-11 %) changes it; gmsh 2D is single-threaded. No ADR is needed: the
original criterion holds with about 2x margin.

## Proposed change (oa-pi owner / integrator; WP-211 does not own oa-pi)

In `test_three_hundred_thousand_unknowns_solve_under_20_s` replace the two timing asserts with
`assert dt < 20.0, (dt, sol.timings)` and mark the TASKS WP-204 criterion "total < 20 s" as met.
