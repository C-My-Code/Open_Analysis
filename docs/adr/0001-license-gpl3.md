# ADR-0001: License is GPL-3.0-or-later, monorepo-wide

- Status: Accepted (user decision, 2026-10-27)
- Deciders: project owner

## Context
- The suite integrates with KiCad (GPL-3), openEMS (GPL-3) and gmsh (GPL-2+ with exception).
- It can reuse the KiCad `pcb_calculator` and Qucs transcalc transmission-line code and SignalIntegrity (GPL-3+).
- The research reports (01-07) weighed a permissive core against GPL. Several of them recommended "reimplement, do not copy GPL code" for the case where the core was permissive.

## Decision
- All Open_Analysis code is **GPL-3.0-or-later**, with REUSE/SPDX headers (`SPDX-License-Identifier: GPL-3.0-or-later`) and a `LICENSES/` folder.
- Porting or vendoring GPL-3-compatible code is **allowed**: KiCad, Qucs/Qucs-S, SignalIntegrity, openEMS `nf2ff`, and others. Original copyright lines are kept, and the file gets a `Ported-from: <project> <path> @ <commit>` comment.
- GPL-2.0-**only** code (e.g. EMerge) must not be linked. It may run as a subprocess. AGPL code (Ki-PIDA) is used only as an external oracle.
- Non-OSI licences (Triangle) and "no commercial inclusion" riders (KiPI) are prohibited as dependencies or code sources. They may serve only as external test oracles.
- Contributions use DCO sign-off (no CLA).
- CI runs `reuse lint` and `pip-licenses` against an allow-list.

**Proposed refinement (pending owner confirmation, see TASKS open questions):** license the generated JSON Schemas (`schemas/`) and the synthetic test boards (`tests/boards/`) additionally under CC-BY-4.0, so that other tools (Z0 Suite, KiPI, kicad-rfsim) can adopt the board model and fixtures. The code stays GPL only.

## Consequences
- In-process use of gmsh and any GPL-compatible library raises no license question.
- Subprocess boundaries (ARCHITECTURE §5) are chosen for robustness and platform reasons, not license reasons.
- The suite cannot be embedded in closed-source products. Commercial *use* is unrestricted.
- The research reports' "do not copy" warnings are superseded. Reimplementing from papers is still preferred where KiCad or Qucs code is unvalidated.
