# oa-em-openems

openEMS (FDTD) solver adapter, WP-108. Implements `oa_framework.em.SolverAdapter`
(interfaces.md section 5.3, ADR-0004). GPL-3.0-or-later; openEMS itself runs as a **separate
process** (a generated script run by the openEMS Python interpreter) and is never imported here.

## Public API

```python
from oa_em_openems import OpenEMSAdapter, discover

adapter = OpenEMSAdapter(farfield=False, assessor=None)  # assessor: skrf.Network -> QualityReport
adapter.capabilities()  # fullwave_driven; lumped + wave (tl_probe) ports; one excitation per run
adapter.check(problem)  # unsupported features as Diagnostics (errors block prepare)
run = adapter.prepare(problem, workdir, tool_info)  # one ExternalCommand per excitation
# ... the job system runs run.commands through an ExternalRunner ...
out = adapter.collect(problem, run, results)  # EMSolveOutput: network, Z0 table, convergence
```

`prepare` writes `workdir/exc_<port>/{scene.json, sim.py}` and returns commands
`openems-python sim.py` (progress parser `openems`, outputs `ports.npz`, `run.json`).
`PreparedRun.mesh_stats` carries cells (+PML cells), min/max cell, grading, estimated memory
and time, the Debye fit errors and the mesh hash. `collect` writes `workdir/out/<problem>.sNp`
(Touchstone, RI, 12 digits) and `<problem>.json` (problem/structure keys, mesh stats, wall time,
quality report, region provenance).

## Translation (EMProblem to openEMS)

- **Mesh** (`mesh.py`, `scene.py`): graded rectilinear lines. Max cell lambda_min/N with N = 10 /
  20 / 30 for coarse / medium / fine (or `cells_per_wavelength`); third rule at axis-aligned metal
  edges (1/3 cell inside, 2/3 outside); at least 4 cells across every dielectric layer and across
  via diameters; growth limited to `max_grading` (1.3) in graded regions (up to ~1.5 across
  fixed lines, the achieved value is in the stats).
- **Copper** (`openems.conductor_model`):
  - `skin` (default, WP-510): copper at its real thickness. A PEC core shorts the two faces through
    the thickness (priority 10); `ConductingSheet` skins with the real conductivity and thickness
    (priority 11) cover the top face, the bottom face and the axis-aligned side walls, so openEMS
    applies its own skin-effect model to each face. The z mesh gets lines on both faces. A face
    on a PEC domain wall gets no skin. Side walls of non-axis-aligned edges are not drawn (warning
    `OPENEMS.SKIN_WALLS`). `openems.skin_freq` [Hz] > 0 instead sets t = delta(f), a flat Rs(f);
    that over-estimates the loss by about 1.4 at 10 GHz and is kept only for experiments. Without
    the PEC core the space between the skins carries a spurious inner TEM mode (resonances of
    several tenths of a dB), which is why the core is needed.
  - `sheet`: one zero-thickness `ConductingSheet` on the dielectric face (or mid-layer); no
    thickness, so Z0 is about 4 % off the 35 um line.
  - `pec`: thick lossless copper.
  Polygons with holes (antipads) become keyhole polygons. Vias are solid PEC cylinders.
- **Dielectrics**: a multi-pole Debye fit plus conductivity over `[f_max/20, f_max]`
  (`openems.dispersion = "debye"`, ADR-0010 section 5; relaxation times on a log grid, two poles
  per decade, non-negative least squares): 6 poles follow Djordjevic-Sarkar to 0.05 % in tan(d)
  over 10:1 and 20:1 bands. `"debye1"` is the old single pole (about 11 % in tan(d), warning
  above 10 %), `"constant"` holds the values at band centre. Resin fills copper layers between
  equal slabs.
- **Boundaries**: `pec`/`pmc` map directly; `absorbing` is `PML_8` (`openems.absorbing = "MUR"`
  possible). Geometry reaching an absorbing face is extended through the padding (lambda/4 at
  f_max unless `air_padding`).
- **Ports**: lumped ports are `LumpedPort` with R = z0. Wave ports are `tl_probe` ports
  (`MSLPort`, `StripLinePort`): the feed line is added outside the box (length max(4 W, 6 h, 8
  cells)), reference plane on the face, shifted by `deembed`. Line type and strip/reference
  heights come from the geometry. Coax, CPW, multi-conductor/multi-mode and asymmetric stripline
  ports are reported as errors by `check`.
- **Excitation**: Gaussian pulse without DC content by default (`openems.pulse = "bandpass"`; the
  tutorial's `baseband` pulse leaves static charge on strips that no port drains), energy end
  criterion `convergence.energy_decay_db`, `max_timesteps`.
- **S-parameters** (`postprocess.py`): from the V and I of all ports in all N runs with power
  waves, `S = B A^-1`; this renormalizes exactly to any real reference and does not assume the
  other ports were matched. `z0=None` wave ports are referenced to the median measured line
  impedance (Touchstone/`NetworkResult` use that constant; the Z0(f) table is
  `EMSolveOutput.port_impedance`).
- **Quality gate**: `oa_network.quality.assess` is called through the contract; without it the
  verdict is forced to `fail` (see `docs/change-requests/WP-108-solver-output-quality-gate.md`).
- **Far field** is behind a flag (`OpenEMSAdapter(farfield=True)` or `openems.farfield`): NF2FF box,
  `FarFieldResult`. Not validated against a reference antenna yet.

Expert keys in `MeshSettings.expert`: `openems.conductor_model`, `openems.dispersion`,
`openems.absorbing`, `openems.port_length` [m], `openems.pulse`, `openems.farfield`.

## Installing openEMS (v0.37.0, Windows)

Used for development; nothing is installed system-wide. From the repository root:

```powershell
# 1. download the official release and verify the published SHA-256
curl -L -o .oa/tools/openems/dl/openEMS_v0.37.0.zip `
  https://github.com/thliebig/openEMS-Project/releases/download/v0.37.0/openEMS_x64_v0.37.0_msvc.zip
# expected: e4f14fd6e9be7d0c1fd9c244768c21c1c2b62b8aa81f08253ebf6e06040cda5a
# 2. extract into its own directory (a zip is untrusted: check member paths first)
#    -> .oa/tools/openems/v0.37.0/openEMS/   (contains openEMS.exe, CSXCAD.dll, python/*.whl)
# 3. a project-local Python 3.13 venv (the release ships cp313/cp314 wheels only)
$env:UV_PYTHON_INSTALL_DIR = "$PWD\.oa\tools\python"
uv venv --python 3.13 .oa/tools/openems/venv
uv pip install --python .oa/tools/openems/venv/Scripts/python.exe numpy h5py scipy matplotlib
uv pip install --python .oa/tools/openems/venv/Scripts/python.exe --no-index `
  --find-links .oa/tools/openems/v0.37.0/openEMS/python openEMS
```

`.oa/` is git-ignored. Discovery (`oa_em_openems.discover`) tries, in order: explicit arguments,
`OA_OPENEMS_PYTHON` + `OA_OPENEMS_HOME` (or `CSXCAD_INSTALL_PATH`), `tools.toml`
(`[tools.openems-python] path = ..., home = ...`), the project-local `.oa/tools/openems`, then
`openEMS` on `PATH`. The runner must set `CSXCAD_INSTALL_PATH` to the folder with `CSXCAD.dll`;
`prepare` does that in `ExternalCommand.env` for local tools.

## Tests

- Unit (no openEMS): mesh rules, geometry/ports translation, dispersion, post-processing,
  Touchstone, snapshots of `prepare()` for the 3 EMProblem fixtures
  (`tests/golden/em_openems/snapshot_*`), `collect()` on committed raw output
  (`tests/golden/em_openems/raw/microstrip_thru`, reproduces `collect_microstrip_thru/expected.s2p`
  to 1e-9).
- `external("openems")` (nightly, slow): the microstrip thru fixture end to end through
  `oa_testkit.harness.run_inline_full` and a local subprocess runner shim. Needs openEMS on `PATH`
  or importable, e.g. `PATH=.oa/tools/openems/v0.37.0/openEMS:$PATH pytest -m external`.
- Regenerate snapshots with `pytest --update-golden`; regenerate the raw data by running the
  microstrip fixture and copying `exc_*/ports.npz`, `run.json` and the last 200 stdout lines.

## Accuracy against the 2D reference (WP-510)

B-EM-01 (canonical 25 mm microstrip, 35 um copper, Djordjevic-Sarkar, lumped 50 ohm ports,
`tests/benchmarks/references/xs/R1`): with `skin` copper and the multi-pole fit the insertion loss
is within 0.03 dB of R1 from 1 to 10 GHz, and within 0.05 dB to 13 GHz; the difference then grows
as f squared to +0.13 dB at 20 GHz, which is the part of the 3D problem the quasi-static R1 does
not contain (dielectric and geometric dispersion of eps_eff, FDTD numerical dispersion). Run time
is 17-22 s per excitation for 0.05-20 GHz, 182k-223k cells, under 100 MB. Run it with
`OA_RUN_SLOW=1 pytest packages/oa-em-openems/tests/test_external_openems_skin.py -m external`
(environment variables in the module docstring).

## Known gaps

- The `skin` model is validated for microstrip only; its side walls follow axis-aligned edges.
- Stripline (closed box) runs do not reach the energy end criterion (stall near -33 dB, slow
  decay of low-frequency energy in the PML) and give unreliable S-parameters at the iteration cap.
  Not validated; only the microstrip-with-lumped-ports path is.
- The MSL-notch tutorial reproduction and the wave-port microstrip Z0-vs-HJ check
  (acceptance, nightly) are not done.
- CPW, coax and coupled/differential wave ports are not implemented.
- Cancellation, memory limits and WSL/Docker path translation belong to the runner (`oa_jobs`).
