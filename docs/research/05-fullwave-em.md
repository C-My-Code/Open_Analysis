# 05 - Full-Wave 3D/2.5D EM Simulation (RF and High-Speed Structures)

Status: research, 2026-10-27. Scope: full-wave extraction of layout regions (via transitions, connector launches, filters, antennas, transitions), S-parameter export, and feedback into the SI engine. 2D impedance, channel simulation, PI and ingest are covered in other reports.

Notes on confidence: facts taken from a cited source are stated plainly. Items marked **[verify]** come from general domain knowledge or partial sources and should be checked before we commit. Runtime and memory numbers in section 5 are **engineering estimates**, not benchmarks. We need to measure them on our own hardware (see the validation plan).

---

## 1. Open-source full-wave solvers

### 1.1 Summary table

| Solver | Method | License | Activity (2026) | Parallelism / GPU | Windows | Python API | PCB fit |
|---|---|---|---|---|---|---|---|
| **openEMS** (+CSXCAD) | FDTD, Cartesian/cylindrical, rectilinear graded mesh | GPLv3 | Active. v0.37.0 released Oct 2026 (new version scheme) | Multithreaded SSE2 engine. **MPI engine removed in v0.37.0.** No GPU | Yes, MSVC builds exist [verify current wheel availability] | Yes (Cython bindings and helpers; new port classes in v0.37) | Very good. Most PCB pipelines target it |
| **AWS Palace** | 3D FEM (MFEM), high-order, AMR | Apache-2.0 | Very active. v0.18.1 released 2026-09-21 | MPI. NVIDIA/AMD GPU, multi-GPU. cuDSS direct solver since 0.18 | Not supported. WSL Ubuntu works per FAQ | No official one (JSON config + mesh). Community wrappers (pypalace, gsim) | Very good for S-params and resonant structures. Needs an external mesher (gmsh) |
| **EMerge** | 3D FEM, time-harmonic Helmholtz | GPL-2.0 | Active, single main developer, NLnet funded | Direct solvers only: PARDISO (Windows), UMFPACK, Apple Accelerate. GPU via cuDSS (experimental) and SPARTA | Yes, first-class (PARDISO built in) | Pure Python, `pip install emerge`. Uses gmsh | Good. Already used by kicad-rfsim. Reads Gerber/ODB++ via `emcad` |
| **Elmer FEM** | FEM, VectorHelmholtz / Whitney AV solvers | GPL (solver), LGPL (library) [verify] | Active (CSC Finland) | MPI | Yes (binaries) | No real Python API (SIF text files) | Weak. Few RF examples, ports and S-parameters are manual (Robin BCs) |
| **MEEP** | FDTD, subpixel smoothing, eigenmode sources | GPL-2.0+ | Active (photonics focus) | MPI | Conda on Linux/macOS. Windows via WSL [verify] | Excellent Python API | Poor. Uniform grid only, so fine PCB features over large domains are expensive. Optics-oriented |
| **NGSolve/Netgen** | FEM toolkit (Nedelec, PML, high order) | LGPL-2.1 [verify] | Active | MPI (ngs-petsc) | Yes (pip) | Excellent Python API | Toolkit only. We would have to write ports, sweeps and post-processing ourselves |
| **FEniCSx (DOLFINx)** | FEM toolkit | LGPL-3.0 | Active. Official PML scattering demo | MPI/PETSc | Conda/WSL. Native Windows is limited [verify] | Excellent | Toolkit only, same as NGSolve |
| **scikit-fem** | Lightweight FEM assembly | BSD-3 | Active | None (SciPy) | Yes | Pure Python | Prototyping and teaching only. Too slow for 3D Maxwell at PCB scale |
| **gprMax** | FDTD (uniform grid) | GPLv3 | Active (GPR focus) | OpenMP and CUDA GPU (up to ~3.4 Gcells/s on Pascal). MPI is used as a task farm | Yes | Python/Cython | Weak. Uniform cubic cells and a ground-penetrating-radar orientation. Its GPU engine is a useful reference design |
| **OpenParEM3D / 2D** | FEM (MFEM), MPI | Open source, but slides say "All Rights Reserved" [verify license] | Small, single author | MPI | Linux | No | Aimed at SI/S-parameters between 2D ports. Worth watching. 2D version is relevant to other agents |

Sources: openEMS README and releases ([repo](https://github.com/thliebig/openEMS), [v0.37.0 release](https://github.com/thliebig/openEMS/releases), [docs](https://openems.readthedocs.io/en/latest/intro.html)); Palace ([repo](https://github.com/awslabs/palace), [docs](https://awslabs.github.io/palace/stable/), [CHANGELOG](https://github.com/awslabs/palace/blob/main/CHANGELOG.md), [FAQ on Windows](https://awslabs.github.io/palace/stable/faq/)); EMerge ([repo](https://github.com/FennisRobert/EMerge), [PyPI](https://pypi.org/project/emerge/), [NLnet](https://nlnet.nl/project/EMerge/), [Amp Hour interview](https://theamphour.com/731-e-m-reality-emerges-with-robert-fennis/)); Elmer ([forum: ports/S-params](https://elmerfem.org/forum/viewtopic.php?p=28383), [forum: PCB traces](https://elmerfem.org/forum/viewtopic.php?p=30517)); MEEP ([Wikipedia](https://en.wikipedia.org/wiki/Meep_(software)), [paper](https://math.mit.edu/~stevenj/papers/OskooiRo10.pdf)); FEniCSx ([PML demo](https://docs.fenicsproject.org/dolfinx/main/python/demos/demo_pml.html)); NGSolve ([forum](https://forum.ngsolve.org/t/using-ngsolve-to-solve-maxwells-equations-in-time-harmonic-form/1090), [DG Maxwell paper](https://arxiv.org/pdf/2002.08733)); gprMax ([GPU docs](https://gprmax.readthedocs.io/en/latest/gpu.html), [CUDA engine paper](https://repository.uwl.ac.uk/id/eprint/5755/)); OpenParEM ([2022 workshop](https://mfem.org/pdf/workshop22/08_Young_OpenParEM2D.pdf), [2024 seminar](https://mfem.org/pdf/seminar/young.pdf)).

### 1.2 Notes per solver

**openEMS (FDTD).** This is the de facto open RF solver for PCBs. It has a graded rectilinear mesh, UPML and Mur absorbers, lumped ports, MSL/stripline/CPW/coax/waveguide ports, lumped RLC elements, Drude/Lorentz/Debye dispersive materials, near-to-far-field (nf2ff) transformation, and HDF5/VTK field dumps. v0.37.0 (Oct 2026) added Python port classes (`CoaxialPort`, `StripLinePort`, `CPWPort`, `CurvePort`, `CircWGPort`), waveguide mode excitation from HDF5 mode files, and localized Mur absorbers. It fixed UPML coefficient and Debye bugs. It also **removed the MPI engine**, so scaling is now limited to one shared-memory node. There is no GPU engine.
- Strengths: broadband in one run, robust, low memory per cell, mature Python API, runs natively on Windows, a large tutorial set (MSL notch filter, patch antenna, IFA, horn).
- Weaknesses: staircased geometry on a Cartesian grid, so diagonal traces and round vias are approximated. Each excited port needs a full run, so an N-port model costs N runs. High-Q filters ring down slowly and take many timesteps. Dielectric loss is fitted at a single frequency, so tan(delta) is accurate only near f0 (kicad-rfsim notes the same limitation). The smallest cell sets the Courant timestep.
- pyems ([matthuszagh/pyems](https://raw.githubusercontent.com/matthuszagh/pyems/master/README.org), forks [GMellar](https://github.com/GMellar/pyems), [ALEEF02](https://github.com/ALEEF02/pyems)) is a higher-level PCB/microstrip wrapper with automatic meshing and KiCad footprint generation. It is largely unmaintained upstream. Use it as a source of ideas, not as a dependency.

**AWS Palace (FEM).** Palace is a parallel FEM code on MFEM. It supports eigenmode, frequency-domain driven (uniform or adaptive fast sweep), time domain, electrostatic/magnetostatic (C/L matrix), and, since 0.17, 2D BoundaryMode analysis. Ports: lumped ports, numeric wave ports (0.17 added impedance BCs on wave ports and mixing lumped and wave ports), surface-current excitation, and Floquet ports (0.17). Boundaries: PEC/PMC, impedance, first- and second-order absorbing. **PML is listed as "coming soon".** Far field: `farfield-rE.csv` is computed from surface integrals over an external absorbing boundary (0.18.1 fixed its units). Solvers: matrix-free p-multigrid, AMG, sparse direct (SuperLU/STRUMPACK/MUMPS, and cuDSS since 0.18 [verify list]). Palace does no meshing itself: you supply a Gmsh or other mesh. It has solution-based AMR, high-order and curvilinear elements, ParaView output, and a memory report since 0.16.1. It is Linux/macOS only. Windows means WSL2 or a container.
- Strengths: accurate on curved and fine geometry, efficient for high-Q structures, true wave ports, AMR for convergence, GPU and MPI scaling to the cloud, permissive Apache license, very active development.
- Weaknesses: no Windows build, no Python API (JSON in, CSV/VTK out, which is actually easy to wrap), meshing is our problem, absorbing BCs instead of PML need a larger air box, and the documentation is skewed toward superconducting quantum circuits.

**EMerge (FEM, Python).** EMerge is a pure-Python, time-harmonic FEM solver with Rust/numba kernels and gmsh for geometry and meshing. It targets RF filters, PCB interconnects and antennas, and includes adaptive mesh refinement. It is Windows-friendly (PARDISO out of the box), and its GPU path through cuDSS is experimental. kicad-rfsim already runs it as an alternative to openEMS (EMerge 2.8.9). It is a direct-solver-only design, so memory limits model size to roughly single-workstation problems. It has a single main maintainer and is GPL-2.0. It is the fastest route to a FEM solver that works on Windows today.

**Elmer, NGSolve, FEniCSx, scikit-fem.** These are general FEM frameworks. Building wave ports, modal solving, S-parameter normalization, fast sweeps and far-field transforms on them would take person-years, and Palace already has all of it. Use them only for niche physics or research. Elmer specifically lacks documented RF port workflows (see forum threads above).

**MEEP, gprMax.** These are uniform-grid FDTD codes. They are not suitable for PCB multiscale geometry: 35 um copper inside a domain of several centimetres. gprMax's CUDA engine shows what a GPU FDTD can reach (~30x over CPU OpenMP) and could inform a future GPU engine.

**Open-source MoM / 2.5D planar (Sonnet/Momentum-like).** No mature, maintained open-source multilayer planar MoM solver for PCBs was found. NEC2/NEC4-style wire codes exist for wire antennas only. A 2026 arXiv paper describes a 2.5D MoM for shielded multilayer PCBs using S-matrix layered Green's functions ([arXiv 2605.18492](https://arxiv.org/abs/2605.18492)), but no code release is evident. Commercial equivalents are Sonnet, Cadence AWR AXIEM ([datasheet](https://www.cadence.com/en_US/home/resources/datasheets/awr-axiem-ds.html)) and Keysight Momentum. **This is a genuine gap.** A 2.5D MoM would be the most efficient tool for planar filters and couplers. We should keep it as a long-term item, possibly in collaboration with the arXiv authors, and not as part of the MVP.

---

## 2. Existing PCB-to-solver pipelines

| Project | Input | Solver | Does well | Missing |
|---|---|---|---|---|
| **gerber2ems** (Antmicro, Apache-2.0) [repo](https://github.com/antmicro/gerber2ems), [blog 2023](https://antmicro.com/blog/2023/11/open-source-signal-integrity-analysis), [2025 update](https://antmicro.com/blog/2025/07/recent-improvements-to-antmicros-signal-integrity-simulation-flow) | Gerbers + Excellon + `stackup.json` + pos file (ports as `SP*` footprints) + `simulation.json` | openEMS | Fab-file input that works with any EDA tool. Adaptive grid around copper (1/3 metal, 2/3 outside cell rule). Differential pairs. CI-friendly. ParaView/Blender export. si-wrapper slices boards around nets (KiCad 9) | Copper is rasterized from Gerber (pixel size, libcairo limits). Ports are microstrip fragments only. No caps or components. Every open trace end must be ported. No far field. No FEM |
| **kicad-rfsim** (MIT, 2026) [repo](https://github.com/NBalciunas/kicad-rfsim) | Live KiCad 10 board (IPC plugin) | openEMS **or** EMerge | In-editor workflow, click pads to make ports. Lumped, microstrip, CPW, stripline and coax ports. Lumped RLC with parasitics. Touchstone out. Far field (Dmax, efficiency, XPD). Mesh presets in cells/wavelength. Windows | Hobby-scale. Each excited port is a full run. Manual solver install. Loss accurate only at f0. No cluster or job queue. No region clipping beyond a margin |
| **pcbmodelgen** (GPL-3) [repo](https://github.com/jcyrax/pcbmodelgen), [AUR](https://aur.archlinux.org/packages/pcbmodelgen-git) | `.kicad_pcb` | openEMS (Octave/Matlab) | Direct board parsing and automatic mesh lines | Stale. Octave only. Ports are manual coordinates. Build problems ([issue](https://github.com/jcyrax/pcbmodelgen/issues/15)) |
| **kicad2ems** (forum, 2025/26) [thread](https://forum.kicad.info/t/kicad2ems-direct-layout-to-electromagnetic-simulation-tool/72007) | KiCad | openEMS | Demo of Wilkinson splitter S-parameters | Early, maturity unknown |
| **hyp2mat** [repo](https://github.com/koendv/hyp2mat) | HyperLynx `.hyp` | openEMS (Octave) | Generic stackup and polygon import | Old. Octave only |
| **Qucs-RFlayout + Qucs-S** [Open-RFlab](https://github.com/Open-RFlab/Open-RFlab), [openEMS/Qucs-S tutorial](https://openems.readthedocs.io/en/latest/concepts/postproc/qucs.html), [Qucs-S 24.2 paper](https://repository.londonmet.ac.uk/9551/) | Qucs microstrip schematic | openEMS | Schematic, then layout, then EM, then Touchstone back into the circuit simulator | Microstrip only. Schematic-driven, so it cannot handle arbitrary layout |
| **gds2palace** (IHP SG13G2) [repo](https://github.com/VolkerMuehlhaus/gds2palace_ihp_sg13g2), [IHP docs](https://ihp-open-pdk-docs.readthedocs.io/en/latest/em_simulation.html) | GDSII + stackup | Palace | Layout to gmsh to Palace, plus `combine_snp` turning `port-S.csv` into Touchstone. Closest reference for a layout-to-Palace flow | RFIC, Linux only, gdspy based |
| **gsim** (gdsfactory) [PyPI](https://pypi.org/project/gsim/) | gdsfactory components | Palace (and others) | gmsh meshing with quality presets, cloud-run model | Photonics/RFIC oriented |
| **pypalace** [user guide](https://pypalace.readthedocs.io/en/stable/user_guide.html) | Python | Palace | Pythonic config and `sim.run(n=8)` with MPI ranks | Small project, qubit-oriented |

**No KiCad-to-Palace pipeline exists today.** The gds2palace and gsim mesh/config patterns can be reused, and we can generate the same Gmsh OCC geometry from our own layout model.

Common gaps across all pipelines, and our opportunity:
1. None start from a vendor-neutral layout database with net-aware clipping and ports at cut boundaries.
2. None offer both FDTD and FEM behind one interface with cross-validation.
3. None run convergence studies automatically or apply passivity/causality checks.
4. None feed results back into an SI channel engine as a cascaded, de-embedded block.
5. None have a job queue or remote/cloud execution.

---

## 3. Pipeline requirements (layout model to S-parameters)

### 3.1 Region and net selection (cut-out)
- Inputs: selected nets (signal plus their reference nets), a seed (for example, a via or footprint), and either a user polygon or an automatic bounding region. Automatic region rule: signal features plus a margin of **max(5*h_dielectric, 3*w_trace, lambda_min/10)**. Cut perpendicular to transmission lines so a port can sit on a uniform section at least 3 to 5 line widths, or at least 8 cells, from any discontinuity (gerber2ems uses a port length of 8 or more cells).
- Copper inside the region: keep all copper on all layers (planes matter for return paths). Classify nets as *signal*, *reference* (GND/PWR planes, treated as AC ground) and *floating/other*. Optionally simplify far-away copper to solid planes.
- Stitching and return vias near signal vias must be kept. Antipads, voids and plane splits must be kept exactly.
- Components: an open RF/SI pipeline needs lumped RLC sheets for SMD parts (caps, resistors, ferrites), as kicad-rfsim does. Parts with S-parameter models are replaced by internal ports and stitched in later at circuit level.

### 3.2 Geometry extrusion
- Use 2D polygon boolean operations (Shapely/Clipper) on the layout model, then extrude per stackup layer.
- **FEM path:** gmsh OCC kernel. Extrude copper (thick, or as zero-thickness PEC/impedance sheets for thin copper above about 1 GHz, which is a large mesh saving), dielectric slabs, via barrels (cylinders or polygons, with plating thickness or filled), pads and antipads. Then `BooleanFragments` for a conformal, shared-face mesh. That conformity is the step Elmer users trip over, per the forum. Tag physical groups for materials, ports and boundaries.
- **FDTD path:** CSXCAD primitives (polygons extruded per layer, cylinders for vias) with priorities. No boolean operations are needed, because priority resolves overlaps.
- Shared requirement: one intermediate geometry description (layered polygons + via cylinders + materials + ports) that both backends consume. This is the central design artifact.
- Materials: frequency-dependent epsilon_r and tan(delta) (Djordjevic-Sarkar wideband Debye model), copper conductivity with roughness (Huray or Groisse) as a surface impedance in FEM. openEMS has no surface roughness, so use effective conductivity [verify].

### 3.3 Ports
| Port type | Use | openEMS | Palace | EMerge |
|---|---|---|---|---|
| Lumped (edge/gap) | SMD pads, component placeholders, antenna feeds | yes | yes | yes |
| Transmission-line port (MSL/stripline/CPW, voltage/current probes) | Trace ends on a cut boundary | yes (MSLPort, StripLinePort, CPWPort) | via wave port | via wave port |
| Wave port (2D eigenmode at cut face) | Accurate de-embedded S-params at a cut boundary, coupled and differential modes | partial (mode file excitation, v0.37) | **yes, numeric wave port** | yes [verify mode solver details] |
| Coax port | Connector launch, through-hole pins | yes (CoaxialPort) | wave port on coax face | yes |
| Via-to-plane / plane-pair port | PI coupling, cavity modes at vias | lumped between planes | lumped | lumped |
| Floquet | Arrays, FSS | no | yes (0.17) | yes [verify] |

Rules: port reference must be the nearest reference conductor (the adjacent plane or the CPW grounds). For wave ports on microstrip, the port face must hold the mode but must not reach the walls, or it excites parallel-plate modes (a typical size is about 5w to 10w wide and 6h to 10h tall [verify]). Differential pairs use mixed-mode post-processing (scikit-rf) or two-mode wave ports.

### 3.4 Boundary conditions
- Closed SI regions (stripline, via fields in a plane sandwich): PEC/PMC or absorbing side walls at the cut planes. Place them at least about 5*h from the structure, or put ports right at the cut.
- Open structures (microstrip, antennas): air box of at least lambda/4 at f_min in FEM with first- or second-order absorbing BCs (Palace, no PML yet). In openEMS use an 8-cell PML (UPML) or Mur, with a lambda/4 clearance for nf2ff.
- Substrate truncation for antennas: model the real board outline, since board size changes antenna results.

### 3.5 Meshing strategy and density rules
- FDTD (openEMS): maximum cell of **lambda_min/20** in the densest dielectric (kicad-rfsim presets run from 10 to 80 cells per wavelength). Use a third-rule at metal edges (1/3 inside, 2/3 outside), at least 3 to 4 cells across dielectric thickness, and a grading ratio of 1.3 or less. Vias need at least 4 to 6 cells across the diameter. Snap mesh lines to the layout grid. gerber2ems' adaptive grid and openEMS `smooth_mesh_lines` are good starting points. Watch the smallest cell, since the Courant limit means doubling density costs at least 8x in runtime (Antmicro).
- FEM: initial tet size of lambda/10 with p=2 elements. Local sizing on edges (w/4), via barrels (at least 8 to 12 segments) and thin dielectrics (at least 2 layers). Then run **AMR on S-parameters at f_max** (Palace AMR, EMerge adaptive). Zero-thickness conductors cut DOF counts sharply.
- Expose mesh presets (coarse/medium/fine) plus an expert override, and log cell and DOF counts.

### 3.6 Convergence and quality checks
- FEM: adaptive refinement until max |delta S| < 0.02 (linear) between passes at f_max (HFSS-style delta-S), with a maximum pass count.
- FDTD: energy decay end-criterion of -40 to -50 dB. Mesh refinement study (run two densities and compare). Check that timesteps cover at least 3x the pulse length (gerber2ems).
- Both: **passivity** (singular values of S at most 1; kicad-rfsim warns on power gain), **reciprocity** (S = S^T), **causality** (Kramers-Kronig or delay check per IEEE 370 quality metrics), DC-point extrapolation for time-domain use, and port impedance sanity (compare Z0 against the 2D impedance solver from the other workstream).
- Cross-solver check: run FDTD vs FEM on a sample of jobs, and flag differences above a set threshold.

### 3.7 De-embedding
- Port reference-plane shift for uniform lines (from gamma at the wave port, or from FDTD line probes).
- 2x-thru de-embedding (IEEE 370 / `skrf.calibration.IEEEP370_SE_NZC_2xThru`), using a simulated 2x-thru of the same lead-in ([scikit-rf notebook](https://scikit-rf.readthedocs.io/en/latest/examples/networktheory/IEEEP370%20Deembedding.html)).
- TRL is an option for measurement comparison.

### 3.8 Export and SI feedback
- Write Touchstone v1.1 and v2.0 (`.sNp`) with port names, reference impedances and mixed-mode tables, using scikit-rf. Palace gives `port-S.csv`, and gds2palace's `combine_snp` shows the conversion. openEMS gives voltages and currents per port, which we convert.
- Metadata sidecar (JSON): region polygon, nets, port-to-net/pin map, stackup hash, mesh stats, convergence history, solver/version, and the passivity/causality results.
- SI engine hand-off: the SI channel engine replaces the extracted region's 2D-line segments with the S-parameter block at matching port reference planes. It should apply passivity enforcement and rational fitting (vector fitting via scikit-rf) when it needs a time-domain/IBIS-AMI flow. A cache key of geometry hash + stackup + frequency plan avoids re-running unchanged regions.

---

## 4. Antenna and RF outputs

| Output | openEMS | Palace | EMerge | kicad-rfsim |
|---|---|---|---|---|
| S11/Zin/VSWR | yes | yes | yes | yes |
| Far field (3D pattern, cuts) | nf2ff box (`CreateNF2FFBox`, `CalcNF2FF`), multi-frequency | `farfield-rE.csv` from an absorbing outer boundary ([docs](https://awslabs.github.io/palace/stable/features/farfield/)) | yes [verify] | polar, 3D, Dmax, co/cross-pol, XPD |
| Directivity / gain / radiation efficiency | Dmax, Prad, efficiency = Prad/Pacc from nf2ff | Compute from rE plus port power (our own post-processing) | [verify] | Dmax and efficiency |
| Field visualization | HDF5/VTK dumps to ParaView (can reach hundreds of GB; gerber2ems warns) | ParaView VTU (E, B, S, Js, port modes) | PyVista built in | E/H mid-plane, currents |
| SAR | yes (IEEE/IEC 62704-1, v0.37) | no | no | no |

Implementation: one post-processing module computes gain, realized gain (with mismatch), efficiency, front-to-back, beamwidth and axial ratio from a normalized far-field grid (theta, phi, E_theta, E_phi). Each adapter converts its native output into that grid. Visualization uses PyVista/VTK in our GUI and ParaView for experts.

---

## 5. Compute feasibility on a desktop workstation

Assumed workstation: 16 to 24 cores, 64 to 128 GB RAM, optional 24 GB GPU. **All figures below are estimates** built from cell/DOF counts and typical throughput: openEMS reaches roughly 50 to 200 Mcell-updates/s multithreaded on such CPUs and uses about 100 to 150 B/cell [verify both by benchmark]. FEM memory with a direct solver is roughly 1 to 3 kB per DOF for p=2 [verify].

| Case | FDTD (openEMS) | FEM (Palace / EMerge) |
|---|---|---|
| **Single-ended via transition**, 8-layer, DC to 40 GHz, region about 6x6x2 mm | 1 to 4 M cells (25 um min cell). About 50k to 150k timesteps. **~5 to 30 min per excited port**. 2-port gives 2 runs. RAM < 1 GB | 0.3 to 1 M tets, p=2, so about 2 to 6 M DOFs. Direct solve at 10 to 40 GB, or iterative with AMG/p-multigrid at < 16 GB. 10 to 25 frequency points with adaptive sweep. **~10 to 60 min total** plus AMR passes |
| **Differential via pair**, 4-port | as above, x4 runs (~0.5 to 2 h) | same mesh, 4 RHS per frequency (cheap with a direct solver). ~20 to 90 min |
| **10 GHz edge-coupled / hairpin filter**, 20x10 mm on 0.5 mm substrate, 2-port, high Q | 2 to 6 M cells. Long ringdown from high Q needs 100k to 300k+ steps. **~0.5 to 3 h**, sensitive to end criterion | 0.5 to 2 M DOFs. Adaptive fast sweep resolves resonances efficiently. **~5 to 30 min**. FEM is the better fit |
| **2.4 GHz PCB antenna** (IFA/MIFA or patch) with board, lambda/4 air box | openEMS patch tutorial: 200x200x150 mm box, 30k steps cap. 0.2 to 2 M cells. **~2 to 20 min** for S11 plus far field in one run | Large air volume. 1 to 3 M DOFs at p=2. ~10 to 40 min. Absorbing BC (no PML) means a bigger box |

Conclusions: all three classes are **feasible on one workstation**. FDTD is the better choice for broadband via/connector work (one run per port, low memory). FEM is the better choice for high-Q filters, curved geometry and multi-port models. GPU: openEMS has no GPU engine. Palace (CUDA/HIP, cuDSS) and EMerge (cuDSS, experimental) do.

Offload options:
- **Palace on AWS** (ParallelCluster/Batch with Spack-built containers, the reference deployment in the [AWS launch blog](https://www.amazonaws.cn/en/blog-selection/aws-releases-open-source-software-palace-for-cloud-based-electromagnetics-simulations-of-quantum-computing-hardware/)) or any Slurm HPC with MPI, multi-GPU.
- **openEMS**: no MPI any more (removed in v0.37), so offload means **farming out independent jobs** (ports, sweeps, design variants) to many nodes. That is trivially parallel and fits a queue.
- Our job queue should treat "where to run" (local, WSL/Docker, SSH/Slurm, cloud batch) as a pluggable executor.

---

## 6. Recommendation

### 6.1 Solver choice
1. **openEMS first (FDTD).** It runs natively on Windows (our users are KiCad/Windows-heavy), has a mature Python API, broadband results, low memory, nf2ff far field, and the most PCB prior art (gerber2ems, kicad-rfsim) to check against. Use it for via transitions, connector launches, broadband trace/via regions and antennas. GPLv3 is fine if we call it as a **separate process** (see architecture). Do not link it into a permissively licensed core.
2. **Palace second (FEM).** It is Apache-2.0, has true numeric wave ports, AMR-based convergence, adaptive fast sweep (high-Q filters), MPI/GPU scaling to cloud, and the fastest development pace in open-source CEM. Run it on Linux/WSL2/Docker and on HPC. Use it for filters, multiport/differential extraction with wave ports at cut boundaries, high-accuracy sign-off, and as the cross-check for openEMS.
3. **EMerge as an optional Windows-native FEM adapter.** It is cheap to add after the gmsh geometry exists, because it shares the same gmsh geometry. It covers users without WSL. GPL-2.0 is fine through a subprocess. Risk: single maintainer.
4. Do not invest in Elmer, MEEP, gprMax or general FEM toolkits for the MVP. Track OpenParEM and the 2.5D MoM paper as a future planar-solver option.

### 6.2 Integration architecture
```
Layout model (ingest agent) ──> Region extractor (nets, clip polygon, port placement)
          │
          v
  EMProblem (solver-neutral IR, JSON/Pydantic):
   layers/polygons/vias/materials(f-dependent)/ports/boundaries/freq plan/mesh preset/convergence goals
          │
   ┌──────┴───────────┬──────────────────┐
   v                  v                  v
 OpenEMSAdapter    PalaceAdapter      EMergeAdapter      (SolverAdapter interface:
 (CSXCAD script    (gmsh OCC -> .msh  (gmsh -> EMerge      prepare() -> job dir,
  + mesh rules)     + config.json)     python script)      run(executor), collect() -> Result)
          │                  │                  │
          └────────── Job queue / executors ─────┘  local process | WSL/Docker | SSH/Slurm | cloud batch
                               │
                               v
   Result normalizer: S(f) (scikit-rf Network), port map, far-field grid, field file refs,
   convergence log -> checks (passivity, reciprocity, causality) -> Touchstone + JSON sidecar
                               │
                               v
                 SI engine / RF post-processing / GUI viewers (PyVista)
```
- **Subprocess, not library.** Each job is a self-contained directory (inputs, generated script/config, logs, outputs). This isolates GPL code, makes Linux-only Palace usable from Windows through WSL/Docker, gives crash isolation, and makes runs reproducible and cacheable. Even for openEMS, run the generated Python script in a separate interpreter or environment.
- **Job queue:** start with a local SQLite-backed queue and worker pool (or `concurrent.futures` + persisted state), with cancel/resume and progress parsing from solver logs. Executors are plugins. A content hash of the EMProblem is the cache key.
- **Solver versions pinned** in container images (Palace via Spack container; openEMS v0.37.x). gerber2ems already pins an openEMS commit for this reason.

### 6.3 MVP scope (about 3 to 4 months, one or two engineers [estimate])
1. EMProblem IR and region extractor: net selection, rectangular/polygon clip, automatic ports on cut traces (MSL/stripline), lumped ports on pads, and via/antipad/plane preservation.
2. openEMS adapter: mesh rules (third-rule, grading, via cells), MSL/stripline/lumped/coax ports, PML/Mur, energy end-criterion, nf2ff.
3. Results: Touchstone export, JSON sidecar, passivity/reciprocity checks, reference-plane shift, 2x-thru de-embedding (scikit-rf), far-field gain/efficiency module.
4. Local job queue with Windows-native execution and a mesh preview (PyVista).
5. Validation suite v1 (below) in CI at coarse mesh, and nightly at fine mesh.
- Phase 2: Palace adapter (gmsh OCC builder, wave ports, AMR, WSL/Docker/Slurm executors), cross-solver regression, an EMerge adapter, and SI-engine cascading with rational fitting.

### 6.4 Validation cases
1. **Microstrip line and stripline thru** (analytic Z0 and loss, compared against the 2D solver from the other workstream). This is a sanity check for port normalization.
2. **openEMS MSL notch filter tutorial** and **Simple Patch Antenna (2.4 GHz)** tutorial ([tutorial](https://openems.readthedocs.io/en/latest/python/openEMS/Tutorials/Simple_Patch_Antenna.html)). These are reproducible reference outputs. Compare our generated model against the hand-written tutorial.
3. **Rectangular inset-fed patch antenna** with published measurements (for example, the Balanis textbook design examples, or a published FR-4 2.4 GHz patch with measured S11 and gain [select a specific paper]). Target: f_res within 1%, gain within 0.5 to 1 dB.
4. **Via transition with measured data:** the IEEE 370 plug-and-play fixtures and the 2x-thru/DUT data in the IEEE 370 supplementary files ([IEEE 370 page](https://standards.ieee.org/ieee/370/6165/), [opensource.ieee.org/elec-char/ieee-370](https://opensource.ieee.org/elec-char/ieee-370), [SI Journal overview](https://www.signalintegrityjournal.com/articles/1169-ieee-p370-a-ffixture-ddesign-and-ddata-qquality-mmetric-sstandard-for-iinterconnects-up-to-50-ghz)). These also exercise our de-embedding and IEEE 370 quality metrics. Also consider Antmicro's measured RDIMM DDR5 tester traces ([blog](https://antmicro.com/blog/2025/07/recent-improvements-to-antmicros-signal-integrity-simulation-flow)), since the board is open hardware [verify data availability].
5. **10 GHz coupled-line/hairpin bandpass filter**: compare openEMS vs Palace vs a published measured design. Tests high-Q convergence.
6. **2.4 GHz MIFA** (TI DN007/AN043-style reference design with measured data [verify document]): realistic PCB antenna with ground clearance. Compare against kicad-rfsim output.
7. Cross-solver matrix: every case on two solvers, with tolerances recorded per case.

---

## 7. Risks
- **Single-node FDTD:** openEMS dropped MPI in v0.37 and has no GPU engine. Large regions or very fine features can take hours. Mitigations: job farming, aggressive region clipping, Palace for large problems, and possibly a GPU FDTD engine later (gprMax shows ~30x is possible).
- **Palace on Windows:** no native support. We depend on WSL2/Docker, which adds install friction and file-system performance issues across `/mnt/c`. There is no PML yet, so air boxes are larger.
- **Meshing robustness:** gmsh OCC boolean operations on real PCB geometry (thousands of polygons, slivers, arcs) are the main engineering risk. EMerge's author estimates "98% of the effort" in a solver is computational geometry. Plan for polygon healing and simplification.
- **Port definition errors** (wrong reference, wave-port size, parallel-plate modes) are the most common source of wrong answers. Automated checks and preview are mandatory.
- **Material models:** openEMS fits dielectric loss at a single frequency and has no surface roughness model, which affects broadband loss accuracy.
- **Licensing:** openEMS GPLv3 and EMerge GPL-2.0 must stay behind a process boundary. Palace (Apache-2.0) has no restrictions.
- **Bus factor:** EMerge and OpenParEM each depend on a single developer. Palace's roadmap is AWS-quantum driven.
- **No open 2.5D MoM:** planar filter workflows will stay slower than Momentum/Sonnet until one exists.
- **Runtime estimates are unverified.** Benchmark week 1 on the validation cases before committing to UX promises.
