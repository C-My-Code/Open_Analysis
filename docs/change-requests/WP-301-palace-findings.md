<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->
# WP-301: contract findings from the Palace adapter

Status: proposal, from WP-301 (Palace adapter). Details of the verified Palace behaviour are in
`packages/oa-em-palace/README.md` ("Resolved [verify] items").

## 1. Memory limits inside WSL belong to the runner (oa_jobs, WP-107)

`WslRunner` samples `wsl.exe` on the host; the Linux process tree is invisible to it and the WSL VM
has no memory cap of its own. The adapter works around this with a wrapper program
(`oa-palace-run`, `systemd-run --user --scope -p MemoryMax=...`) that the user installs in WSL and
names in `tools.toml`. Proposal: `ExternalCommand` gets an optional `limits: {memory_gb, cpu_percent,
ranks}` field that every runner honours (WSL: the same `systemd-run` scope, Docker: `--memory`,
`--cpus`; local Linux: cgroup scope or `prlimit`), and `ExternalResult` gets `wall_s` / `peak_mem_mb`
measured on the Linux side. Impact: additive fields, the adapter then drops the env-var protocol
(`OA_PALACE_MEM_GB` ...).

## 2. `ToolInfo` carries no WSL distro

`tools.toml` has `distro`, but `ToolInfo` does not, so `discover()` of an adapter cannot return a
complete description for the `wsl` runner. Proposal: add `distro: str | None` to `ToolInfo`.

## 3. Wave-port reference impedance

`EMWavePort.z0` documents "None = renormalize to modal Z0". Palace's wave-port S-parameters are
referenced to the port mode's own characteristic impedance (power waves) and Palace 0.18 provides no
Z0(f) table for the mode unless `VoltagePath` (needs GSLIB) is configured. The adapter therefore
cannot honour a fixed `z0` and does not return `port_impedance` for wave ports. Proposal: either
document `z0` as "ignored by modal-port solvers" in interfaces.md section 5, or require adapters to
renormalize (needs Z0(f) from the solver).

## 4. WP-206 `.msh` file header unit

The `$Entities` section of the written `.msh` holds bounding boxes in millimetres (gmsh's internal
unit) while `$Nodes` are in metres. Palace/MFEM ignores the entity bounding boxes, so this is
harmless today, but other readers may trust them. Proposal: WP-206 scales the entity section too
(or writes it without bounding boxes).
