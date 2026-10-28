# Inter-package Contracts v1

Status: **FROZEN with WP-000** for the parts marked *(W0)*. Other sections are binding specifications that the owning work package implements exactly. Any change needs an ADR, except clarifications that do not change signatures.
Companion: [board-model.md](board-model.md) (the `Board` and setup types), [ARCHITECTURE.md](../ARCHITECTURE.md).

Conventions:
- All public types are pydantic v2 models deriving from `oa_model._base.OAModel` (frozen, extra forbidden) unless stated otherwise. Units are SI (board-model.md §2).
- "Protocol" means `typing.Protocol` with `@runtime_checkable`.
- Every public function is fully type-annotated. Packages ship `py.typed`, and mypy runs in `--strict` on `oa_model`, `oa_results` and `oa_framework`.
- Errors derive from `oa_model.errors.OAError` (section 12).

---

## 0. Package map and allowed imports

| Layer | Packages (import name) | May import |
|---|---|---|
| L0 data | `oa_model` | pydantic, numpy, pyyaml |
| L1 foundations | `oa_physics`, `oa_results`, `oa_testkit` | L0; `oa_results` adds scikit-rf, pyarrow; `oa_testkit` may import anything at L0-L1 plus pytest |
| L2 ingest | `oa_ingest_kicad`, `oa_ingest_ipc2581`, `oa_kicad_bridge` | L0, shapely, lxml (ipc2581) |
| L2 derived | `oa_geometry`, `oa_mesh`, `oa_network` | L0, L1, shapely, networkx, gmsh (`oa_mesh` only), scikit-rf (`oa_network`) |
| L3 framework | `oa_framework`, `oa_jobs` | L0-L2 (`oa_jobs` imports `oa_framework`, not vice versa) |
| L4 analyses | `oa_impedance`, `oa_erc`, `oa_pi`, `oa_em`, `oa_em_openems`, `oa_em_palace`, `oa_thermal`*, `oa_qs`*, `oa_emi`*, `oa_si`* | L0-L3. **Never each other**: cross-analysis use goes through `oa_framework.services` (section 3.4). Exception: `oa_em_*` adapters import `oa_em` |
| L5 apps | `oa_cli`, `oa_server` | everything |

\* = later milestones; names reserved now.

These rules are enforced by import-linter contracts in the root `pyproject.toml` (WP-000), which define a `layers` contract plus an `independence` contract over the L4 packages.

---

## 1. CrossSection: input to the impedance solver *(W0, `oa_model.xsection`)*

A 2D description of a transmission-line cross-section. Coordinates are `(u, z)`: `u` is lateral, perpendicular to propagation, and `z` is vertical (same orientation as the board's z). The section is **canonical**: geometry is translated so that `u = 0` is the midpoint of the signal conductors' bounding box and `z = 0` is the lowest material boundary in the section.

```python
# oa_model/xsection.py
class XsTrapezoid(OAModel):
    kind: Literal["trapezoid"] = "trapezoid"
    u_center: float
    z_bottom: float
    w_bottom: Length = Field(gt=0)
    w_top: Length = Field(gt=0)             # == w_bottom for a rectangle
    thickness: Length = Field(gt=0)

class XsPolygon(OAModel):
    kind: Literal["polygon"] = "polygon"
    polygon: Polygon                        # (u, z) coordinates in the Polygon's x,y slots

class XsCircle(OAModel):                    # analytic validation (coax, wire over plane)
    kind: Literal["circle"] = "circle"
    u_center: float; z_center: float; radius: Length = Field(gt=0)

class XsPlane(OAModel):
    """Horizontal ground/reference conductor. u_min/u_max = None means infinite (BEM truncates it
    at `truncation` and reports that it did)."""
    kind: Literal["plane"] = "plane"
    z_bottom: float
    thickness: Length                      # 0 allowed (zero-thickness plane)
    u_min: float | None = None
    u_max: float | None = None

XsShape = Annotated[XsTrapezoid | XsPolygon | XsCircle | XsPlane, Field(discriminator="kind")]

class XsConductor(OAModel):
    id: str                                 # "s1", "s2", "g1", ...: unique in the section
    role: Literal["signal", "ground", "floating"]
    shape: XsShape
    net: NetId | None = None                # provenance only; NOT part of the key
    conductivity: float = 5.8e7
    roughness_top: Roughness = NoRoughness()
    roughness_bottom: Roughness = NoRoughness()
    roughness_side: Roughness = NoRoughness()

class XsSlab(OAModel):
    kind: Literal["slab"] = "slab"
    z_bottom: float; z_top: float
    u_min: float | None = None; u_max: float | None = None   # None = infinite

class XsRegion(OAModel):
    kind: Literal["region"] = "region"
    polygon: Polygon                        # e.g. conformal solder mask (built by geometry with a polygon offset)

class XsDielectric(OAModel):
    region: Annotated[XsSlab | XsRegion, Field(discriminator="kind")]
    model: DielectricModel                  # frequency-dependent (board-model.md §6.1)
    dk_z_over_dk_xy: float = 1.0
    priority: int = 0                       # higher wins where regions overlap; slabs default 0, regions 10

class CrossSection(OAModel):
    schema: Literal["oa-xsection/1"] = "oa-xsection/1"
    conductors: tuple[XsConductor, ...]     # ORDER of signal conductors defines matrix index order
    dielectrics: tuple[XsDielectric, ...]
    background_dk: float = 1.0              # outside every dielectric (air)
    topology_hint: Literal["microstrip", "embedded_microstrip", "stripline", "offset_stripline",
                           "cpw", "cpwg", "edge_coupled_microstrip", "edge_coupled_stripline",
                           "broadside_coupled", "coax", "wire_over_plane", "general"] = "general"
    symmetric_about_u0: bool = False        # solver may exploit it; validator checks it
    provenance: XsProvenance | None = None  # NOT part of the key

    def key(self) -> str: ...               # see below
    def signal_ids(self) -> tuple[str, ...]: ...

class XsProvenance(OAModel):
    board_hash: str | None = None
    net_ids: tuple[NetId, ...] = ()
    layer: LayerId | None = None
    position: Vec2 | None = None            # board-frame sample point
    track_ids: tuple[ItemId, ...] = ()
```

**Key and caching.** `key()` returns `"xs1:" + sha256(...)[:32]` over a canonical encoding:
- Every length is rounded to the nearest **1 nm**.
- Material parameters are rounded to **6 significant digits**.
- `provenance` and `net` are dropped, while conductor **order** and roles are kept.

Two sections with equal keys must give identical solver results. Geometry (WP-104) builds sections already canonicalized via `CrossSection.canonicalize(raw) -> CrossSection`, which is implemented in oa-model.

### 1.1 Impedance service output *(W0, `oa_results.rlgc`)*

```python
class RLGC(OAModel):
    """Per-unit-length matrices on a frequency grid. N = number of signal conductors."""
    freqs: Floats                           # Hz, ascending; may include 0.0 (DC) as first point
    R: NDArray                              # (F, N, N) ohm/m
    L: NDArray                              # (F, N, N) H/m
    G: NDArray                              # (F, N, N) S/m
    C: NDArray                              # (F, N, N) F/m   (Maxwell form, C_ij <= 0 off-diagonal)
    conductor_ids: tuple[str, ...]

class LineParams(OAModel):
    """Derived characteristic values at each frequency (computed by oa_impedance, not consumers)."""
    freqs: Floats
    zc_modal: NDArray                       # (F, N) complex, modal characteristic impedances
    eps_eff_modal: NDArray                  # (F, N) real
    zc_matrix: NDArray                      # (F, N, N) complex
    z0: Floats | None = None                # N == 1: |Zc| (real part reported separately in zc_modal)
    z_diff: Floats | None = None            # N == 2: Zc11+Zc22-Zc12-Zc21 (real part)
    z_comm: Floats | None = None
    z_odd: Floats | None = None; z_even: Floats | None = None
    delay_per_m: NDArray                    # (F, N) s/m per mode
    loss_db_per_m: NDArray                  # (F, N)

class ImpedanceResult(OAModel):
    xs_key: str
    method: Literal["closed_form", "bem", "fem"]
    model_name: str                         # e.g. "hammerstad_jensen_1980", "oa-bem 0.2"
    rlgc: RLGC | None                       # None for closed forms that only give Z0/eeff
    params: LineParams
    validity: Literal["in_range", "extrapolated", "invalid"]
    notes: tuple[str, ...] = ()             # e.g. "plane truncated at 20*b", "mask approximated"
    solver_version: str
```

`NDArray` is the `oa_model.arrays.NDArray` type: an immutable N-D array generalizing `Points`. Its JSON form is `{"dtype": "f8"|"c16", "shape": [...], "data": [...]}`, with complex data interleaved as re, im.

---

## 2. Derived geometry contracts (`oa_geometry`, owned by WP-104)

Analyses and rules consume the board through this service. Every function is pure in the Board (plus any explicit arguments) and is cached by `board.content_hash()`.

```python
# oa_geometry/__init__.py
class BoardGeometry:
    """Built with BoardGeometry.build(board). Holds shapely 2 geometries (GEOS) and STRtrees."""
    board: Board
    @classmethod
    def build(cls, board: Board, *, arc_tol: float = 1e-6) -> "BoardGeometry": ...

    # copper
    def copper(self, layer: LayerId, net: NetId | None = None) -> shapely.MultiPolygon: ...
        # union of tracks (buffered, round caps), pads, via pads, zone fills, shapes for that net/layer
    def copper_by_net(self, layer: LayerId) -> Mapping[NetId | None, shapely.MultiPolygon]: ...
    def voids(self, layer: LayerId, net: NetId) -> shapely.MultiPolygon: ...
        # outline ∩ complement(copper(layer, net)): splits, antipads, slots
    def outline(self) -> shapely.Polygon: ...
    def strtree(self, layer: LayerId) -> "LayerIndex": ...   # query(geom) -> ItemRefs
    def drills(self) -> tuple["DrillGeom", ...]: ...

    # connectivity
    def topology(self, net: NetId) -> "NetTopology": ...
    def islands(self, net: NetId) -> tuple["Island", ...]: ...   # disconnected copper groups

    # return path (shared by ERC/EMI/SI/PI; report 07 §3.1)
    def reference_map(self) -> "ReferenceMap": ...

    # sampling for impedance and profiles
    def segments(self, net: NetId) -> tuple["RoutedSegment", ...]: ...
    def cross_section_at(self, seg: "RoutedSegment", s: float, *,
                         window: float | None = None,
                         include_aggressors: bool = True,
                         physics_temp: float | None = None) -> CrossSection: ...
    def sample_net(self, net: NetId | tuple[NetId, NetId], *, ds: float | None = None
                   ) -> tuple["XsSample", ...]: ...
    # region extraction for 3D (EM, quasi-static, thermal 3D)
    def cut_region(self, spec: "RegionSpec") -> "RegionCut": ...
    def resolve_terminal(self, t: Terminal) -> "ResolvedTerminal": ...

class RoutedSegment(OAModel):
    id: str                                 # "<track_id>" or "<track_id>#k" for sub-pieces
    net: NetId; layer: LayerId; width: float
    seg: PathSeg                            # one line or arc
    length: float
    s_start: float                          # cumulative path length from the topology root

class NetTopology(OAModel):
    """Graph of a net: nodes = pads, vias, junctions (T), track ends; edges = RoutedSegments or
    via barrels. Trees are rooted at the driver pad if intent gives one, else at the lowest-refdes pad."""
    net: NetId
    nodes: tuple["TopoNode", ...]
    edges: tuple["TopoEdge", ...]
    stubs: tuple["Stub", ...]               # branches ending without a pad (and pad stubs)
    def networkx(self) -> "networkx.MultiGraph": ...

class ReferenceSpan(OAModel):
    s_start: float; s_end: float            # along the RoutedSegment
    above: "RefInfo | None"; below: "RefInfo | None"
class RefInfo(OAModel):
    layer: LayerId
    net: NetId
    distance: float                         # vertical distance trace->plane surface
    coverage: float                         # 0..1 fraction of the (W + 2*k*h) footprint covered
    edge_distance: float                    # lateral distance trace edge -> nearest plane edge
class ReferenceMap(OAModel):
    k_buffer: float = 3.0                   # footprint half-width = W/2 + k*h
    spans: tuple[tuple[str, tuple[ReferenceSpan, ...]], ...]   # RoutedSegment.id -> spans
    via_transitions: tuple["ViaTransition", ...]   # signal via: from/to references, return vias/caps near
    def for_segment(self, seg_id: str) -> tuple[ReferenceSpan, ...]: ...

class XsSample(OAModel):
    s: float; position: Vec2; layer: LayerId; seg_id: str
    xs: CrossSection
    event: Literal["regular", "width_change", "ref_change", "layer_change", "neckdown",
                   "plane_edge", "no_reference"] = "regular"

class RegionSpec(OAModel):
    nets: tuple[NetId, ...]                 # signal nets of interest
    reference_nets: tuple[NetId, ...] = ()  # empty = auto from ReferenceMap
    bbox: tuple[float, float, float, float] | None = None   # xmin, ymin, xmax, ymax; None = auto
    margin: float | None = None             # auto = max(5*h_max, 3*w_max, lambda_min/10)
    keep_all_copper: bool = True
    layers: tuple[LayerId, ...] | None = None   # restrict z range; None = full stack
    f_max: Frequency | None = None

class RegionCut(OAModel):
    bbox: tuple[float, float, float, float]
    structure: "Structure3D"                # section 5.1, ready for EM/QS/thermal problems
    boundary_crossings: tuple["BoundaryCrossing", ...]   # traces crossing the box: candidate ports
```

Acceptance contracts for `oa_geometry` are listed in TASKS.md (WP-104).

---

## 3. Plugin protocols *(W0, `oa_framework`)*

### 3.1 Entry-point groups

| Group | Value type | Example |
|---|---|---|
| `open_analysis.ingest` | `Reader` class | `kicad = "oa_ingest_kicad:KiCadReader"` |
| `open_analysis.analyses` | `Analysis` class | `"impedance.xsection" = "oa_impedance.analysis:XsectionAnalysis"` |
| `open_analysis.rules` | `Rule` class | `"RP-001" = "oa_erc.rules.return_path:SplitCrossing"` |
| `open_analysis.services` | service factory | `impedance = "oa_impedance.service:make_service"` |
| `open_analysis.em_adapters` | `SolverAdapter` class | `openems = "oa_em_openems:OpenEMSAdapter"` |
| `open_analysis.qs_adapters` | `ExtractionAdapter` class | `fastercap = "oa_qs.fastercap:FasterCapAdapter"` (M2) |
| `open_analysis.runners` | `ExternalRunner` class | `wsl = "oa_jobs.runners.wsl:WslRunner"` |

`oa_framework.registry.Registry.load()` discovers them through `importlib.metadata.entry_points`. Duplicate names are an error. `Registry.for_tests(analyses=[...], rules=[...], services={...})` builds a registry without entry points.

### 3.2 Ingest reader

```python
class ReadOptions(OAModel):
    project_file: Path | None = None        # .kicad_pro next to .kicad_pcb (auto-detected if None)
    refill_zones: Literal["never", "if_unfilled", "always"] = "never"   # needs oa_kicad_bridge
    arc_tolerance: float = 1e-6
    strict: bool = False

@runtime_checkable
class Reader(Protocol):
    name: ClassVar[str]                     # "kicad", "ipc2581", "altium"
    extensions: ClassVar[tuple[str, ...]]   # (".kicad_pcb",)
    @classmethod
    def can_read(cls, path: Path) -> bool: ...      # cheap sniff (first 4 KB)
    def read(self, path: Path, options: ReadOptions) -> Board: ...
```

`oa_framework.ingest.load(path, options) -> Board` picks a reader by `can_read`. The Altium reader (`oa_kicad_bridge`) converts the file with `kicad-cli pcb import` into a temp dir and delegates to the KiCad reader, setting `meta.source_tool="altium"`.

### 3.3 Analysis protocol

```python
# oa_framework/analysis.py
class Resources(OAModel):
    cpus: int = 1
    memory_gb: float = 1.0
    gpu: bool = False
    needs: tuple[str, ...] = ()             # external capabilities, e.g. ("openems>=0.37",)
    est_seconds: float | None = None

class Task(OAModel):
    """A unit of cacheable work. MUST be JSON-serializable: executors ship it to other processes."""
    id: str                                 # unique in the plan, e.g. "xs:<key>", "port:P1"
    analysis: str                           # Analysis.kind
    payload: dict[str, JsonValue]           # analysis-defined; validated by Analysis.TaskPayload
    inputs: tuple["ArtifactRef", ...] = ()  # files/blobs this task reads (content-addressed)
    depends_on: tuple[str, ...] = ()        # other task ids in the same plan
    resources: Resources = Resources()
    cache: bool = True

class Plan(OAModel):
    tasks: tuple[Task, ...]                 # a DAG; topologically sortable
    reduce_inputs: tuple[str, ...] | None = None   # task ids whose outputs reduce() needs; None = all

class TaskOutput(OAModel):
    task_id: str
    data: dict[str, JsonValue] = {}         # small JSON results
    artifacts: tuple["ArtifactRef", ...] = ()   # large outputs written via ctx.artifacts
    diagnostics: tuple[Diagnostic, ...] = ()

class PlanContext(Protocol):
    board: Board                            # physics already applied
    intent: ResolvedIntent
    setup_hash: str
    def geometry(self) -> BoardGeometry: ...   # lazily built and cached
    def services(self) -> "Services": ...

class RunContext(Protocol):
    task: Task
    scratch: Path                           # empty per-task working directory (deleted on success unless keep=True)
    threads: int
    def board(self) -> Board: ...           # loaded lazily from the job's board artifact
    def geometry(self) -> BoardGeometry: ...
    def services(self) -> "Services": ...
    def progress(self, fraction: float, message: str = "") -> None: ...   # 0..1, rate-limited
    def check_cancelled(self) -> None: ...  # raises Cancelled
    def log(self, level: str, message: str, **fields) -> None: ...
    def artifacts(self) -> "ArtifactStore": ...
    def run_external(self, cmd: "ExternalCommand") -> "ExternalResult": ...   # section 4.3

@runtime_checkable
class Analysis(Protocol):
    kind: ClassVar[str]                     # dotted: "impedance.xsection", "impedance.profile",
                                            # "erc", "pi.dc", "em.sparams", "qs.extract", "thermal.board"
    version: ClassVar[str]                  # bump when results change; part of the cache key
    title: ClassVar[str]
    Config: ClassVar[type[OAModel]]         # setup schema -> UI form via JSON Schema
    TaskPayload: ClassVar[type[OAModel]]
    def validate_setup(self, ctx: PlanContext, cfg: OAModel) -> tuple[Diagnostic, ...]: ...
    def plan(self, ctx: PlanContext, cfg: OAModel) -> Plan: ...
    def run_task(self, ctx: RunContext, payload: OAModel) -> TaskOutput: ...
    def reduce(self, ctx: RunContext, cfg: OAModel,
               outputs: Mapping[str, TaskOutput]) -> "ResultBundle": ...
```

The rules every analysis follows:
- `plan` must be fast (< 1 s typical) and must not do heavy solving.
- `run_task` must be **deterministic** in `(analysis.kind, version, payload, inputs)`, because that tuple is its cache key. It must not read the board through anything except `ctx`.
- It must never import `oa_server`, `oa_cli` or any UI code.
- If `reduce` returns any `NetworkResult`, the result must carry a `QualityReport` produced by `oa_network.quality.assess` (section 4.5). `oa_jobs` rejects bundles that break this rule (`QualityGateMissing`).

### 3.4 Services: cross-analysis calls

```python
class ImpedanceService(Protocol):
    def solve(self, xs: CrossSection, freqs: Sequence[float] | None = None, *,
              method: Literal["auto", "closed_form", "bem", "fem"] = "auto") -> ImpedanceResult: ...
    def solve_many(self, xss: Sequence[CrossSection], freqs: Sequence[float] | None = None, *,
                   method: str = "auto") -> list[ImpedanceResult]: ...   # dedups by key, parallel
    def quick(self, xs: CrossSection) -> ImpedanceResult: ...            # closed form, < 1 ms

class Services(Protocol):
    def get(self, name: str) -> Any: ...    # "impedance" -> ImpedanceService; KeyError if absent
    def has(self, name: str) -> bool: ...
```

`method="auto"` means closed form when `topology_hint` matches a validated formula and no extra conductors are present, otherwise BEM. Services cache by `xs.key()` in the job cache (section 4.4).

Reserved service names:
- `impedance` (M1).
- `via_model` (analytic via R/L/C, M1).
- `reference_map` (alias to geometry).
- `network` (M2).
- `qs_extract` (M2).
- `pdn` (M2).

### 3.5 Rule protocol (ERC and EMI)

```python
# oa_framework/rules.py
Severity = Literal["info", "warning", "error"]

class RuleContext(Protocol):
    board: Board
    intent: ResolvedIntent
    def geometry(self) -> BoardGeometry: ...
    def services(self) -> Services: ...
    def nets_in_scope(self) -> tuple[NetId, ...]: ...      # after the rule's NetSelector and intent

@runtime_checkable
class Rule(Protocol):
    id: ClassVar[str]                       # "RP-001"
    title: ClassVar[str]
    category: ClassVar[Literal["return_path", "edge", "impedance", "diff_pair", "via",
                               "crosstalk", "timing", "power", "rf", "emi"]]
    aliases: ClassVar[tuple[str, ...]] = () # e.g. ("E2",) for the EMI catalog in report 07
    default_severity: ClassVar[Severity]
    requires: ClassVar[frozenset[str]] = frozenset()   # service names, e.g. {"impedance"}
    references: ClassVar[tuple[str, ...]]   # citations for thresholds (report 07 risk 7)
    Config: ClassVar[type[OAModel]]         # thresholds; MUST have defaults for every field
    def check(self, ctx: RuleContext, cfg: OAModel) -> Iterable["Violation"]: ...
```

Rules are pure functions of `(board, intent, cfg)` and must not mutate anything. The ERC analysis (`kind="erc"`) groups rules into tasks, one per rule. Rules that need `impedance` share the service cache.

### 3.6 Rule catalog v1 (unified ERC + EMI)

One rule engine covers both catalogs. Report 07's E-rules are aliases of ERC ids, so a violation is never reported twice.

| ID | Alias | Title | M |
|---|---|---|---|
| RP-001 | E2 | Trace crosses reference-plane split/gap | M1 |
| RP-002 | | Trace over voided reference (antipad/slot cluster) | M1 |
| RP-003 | | Reference net changes along the route | M1 |
| RP-004 | E4 | Layer transition without return via / stitching cap | M1 |
| RP-007 | E3 | Unreferenced high-speed segment | M1 |
| ED-001 | | Trace too close to reference-plane edge | M1 |
| ED-002 | E1 | High-speed trace/via near board edge | M1 |
| ED-003 | E5 | Stitching via spacing > λ/20 (plane edges, guard, RF) | M1 |
| IM-001 | | Width differs from net-class target width | M1 |
| IM-002 | | Computed Z0/Zdiff out of tolerance (impedance service) | M1 |
| IM-003 | | Neckdown longer than max | M1 |
| IM-005 | | Stub longer than max | M1 |
| DP-001/002/003 | | Uncoupled length, intra-pair skew (time domain), gap variation | M1 |
| VI-001 | | Via stub length / stub resonance vs f_max | M1 |
| VI-002 | | Via count per HS net | M1 |
| XT-001 | E8 | Parallel coupled length at spacing | M1 |
| PD-001 | E6 | Decap distance / coverage to IC power pin | M1 |
| RF-001 | | Via fence pitch along RF lines | M1 |
| EM-011 | E11 | Plane-cavity resonance vs clock harmonics | M1 |
| RP-005, RP-006, ED-004, IM-004, IM-006, DP-004, DP-005, XT-002, XT-003, LT-001, LT-002, PD-002, PD-003, RF-002..004, EM-007 (E7), EM-009 (E9), EM-010 (E10), EM-012 (E12, info only), EM-013 (E13) | | see docs/research/06 §3.2 and 07 §3.1 | M2 |
| IM-007, VI-003, VI-004, PD-004 | | | M3 |

### 3.7 Violation *(W0, `oa_results.violations`)*

```python
class Violation(OAModel):
    rule_id: str
    severity: Severity
    message: str                            # one sentence, human-readable
    nets: tuple[NetId, ...] = ()
    layers: tuple[LayerId, ...] = ()
    items: tuple[ItemId, ...] = ()          # board item ids (tracks, vias, pads, zones)
    location: Vec2                          # marker point (board frame)
    geometry: Polygon | None = None         # highlight region (e.g. crossing area)
    measured: float | None = None
    limit: float | None = None
    unit: str | None = None                 # "m", "s", "ohm", "Hz", ...
    fix_hint: str | None = None
    fingerprint: str                        # stable hash (rule_id, sorted items, rounded location 10 um)
    waived: "Waiver | None" = None

class Waiver(OAModel):
    fingerprint: str | None = None          # exact waiver
    rule_id: str | None = None              # or rule + nets glob
    nets: tuple[str, ...] = ()
    reason: str
    author: str | None = None
    expires: str | None = None              # ISO date
```

---

## 4. Results, jobs and executors

### 4.1 Result containers *(W0 types, `oa_results`; writers WP-107)*

```python
# oa_results/bundle.py
class ArtifactRef(OAModel):
    sha256: str
    media_type: str                         # "application/x-npy", "application/vnd.apache.parquet",
                                            # "application/x-touchstone", "model/vtu", "text/plain"
    name: str                               # logical name inside the bundle
    size: int

class ResultItem(OAModel):
    id: str                                 # unique within the bundle
    title: str
    kind: Literal["network", "field", "table", "violations", "profile", "scalars",
                  "rlgc", "farfield", "extraction", "artifact"]
    data: "NetworkResult | FieldResult | TableResult | ViolationSet | ProfileResult | ScalarSet |"
          "RLGCResultSet | FarFieldResult | ExtractionResult | ArtifactOnly"   # discriminated by kind

class Provenance(OAModel):
    analysis: str; analysis_version: str
    board_hash: str; physics_hash: str | None; setup_hash: str
    config: dict[str, JsonValue]
    solver: str | None = None; solver_version: str | None = None
    host: str | None = None
    started_utc: str; finished_utc: str

class ResultBundle(OAModel):
    schema: Literal["oa-result/1"] = "oa-result/1"
    id: str                                 # ULID
    status: Literal["ok", "warning", "failed_quality", "error"]
    summary: str
    items: tuple[ResultItem, ...]
    diagnostics: tuple[Diagnostic, ...] = ()
    provenance: Provenance
```

Typed payloads. UIs render by `kind`, never by analysis.

```python
class NetworkResult(OAModel):               # S-parameters
    kind: Literal["network"] = "network"
    freqs: Floats                           # Hz
    s: NDArray                              # (F, P, P) complex128
    z0: NDArray                             # (P,) or (F, P) complex: reference impedances
    port_names: tuple[str, ...]
    port_map: tuple["PortMapEntry", ...]    # port -> nets, terminals, location
    mixed_mode_pairs: tuple[tuple[int, int], ...] = ()   # (p, n) port index pairs for SDD/SCC
    validity_fmax: Frequency | None = None  # quasi-static or model limit; None = full band
    quality: "QualityReport"                # REQUIRED (gate, section 4.5)
    touchstone: ArtifactRef | None = None   # the .sNp written by oa_results.io
    def to_skrf(self) -> "skrf.Network": ...

class TriMesh2D(OAModel):
    nodes: Points                           # (N, 2) board frame
    triangles: NDArray                      # (M, 3) int64
    layer: LayerId | None = None
    z: float | None = None
class TetMesh3D(OAModel):                   # M3+ (field dumps from Palace/Elmer)
    nodes: NDArray                          # (N, 3)
    tets: NDArray                           # (M, 4) int64
class RasterGrid2D(OAModel):                # thermal FVM and raster DC reference
    origin: Vec2; dx: float; dy: float; nx: int; ny: int; layer: LayerId | None = None

class FieldData(OAModel):
    name: str                               # "V", "dV", "J_mag", "J_vec", "T", "q"
    unit: str                               # "V", "A/m^2", "K", "W/m^2"
    location: Literal["node", "cell"]
    values: NDArray                         # (n,) or (n, k) real
    clim_hint: tuple[float, float] | None = None
    log_scale: bool = False

class FieldResult(OAModel):
    kind: Literal["field"] = "field"
    pieces: tuple["FieldPiece", ...]        # one per layer (2D) or one volume piece (3D)
class FieldPiece(OAModel):
    mesh: TriMesh2D | TetMesh3D | RasterGrid2D
    fields: tuple[FieldData, ...]
    net: NetId | None = None

class ColumnSpec(OAModel):
    name: str; dtype: Literal["f8", "i8", "str", "bool"]; unit: str | None = None
    role: Literal["value", "id", "net", "layer", "item", "location_x", "location_y", "status"] = "value"
class TableResult(OAModel):
    kind: Literal["table"] = "table"
    columns: tuple[ColumnSpec, ...]
    rows: ArtifactRef                       # Parquet file; small tables (<1000 rows) may also inline:
    inline: tuple[tuple[JsonValue, ...], ...] | None = None

class ViolationSet(OAModel):
    kind: Literal["violations"] = "violations"
    rules_run: tuple[str, ...]
    violations: tuple[Violation, ...]
    counts: dict[Severity, int]

class ProfileResult(OAModel):               # impedance/delay vs distance along a net
    kind: Literal["profile"] = "profile"
    net: NetId | tuple[NetId, NetId]
    s: Floats                               # path length (m)
    quantities: tuple[FieldData, ...]       # location="node" over s: "Z0"/"Zdiff", "eps_eff", "loss_db_per_m"
    events: tuple["ProfileEvent", ...]      # via, layer change, ref change, neckdown, plane edge
    target: float | None = None; tolerance: float | None = None

class ScalarSet(OAModel):
    kind: Literal["scalars"] = "scalars"
    values: tuple["Scalar", ...]            # name, value, unit, status(pass/warn/fail), limit

class RLGCResultSet(OAModel):
    kind: Literal["rlgc"] = "rlgc"
    results: tuple[ImpedanceResult, ...]

class ExtractionResult(OAModel):            # quasi-static 3D (Q3D-class), M2
    kind: Literal["extraction"] = "extraction"
    terminals: tuple[str, ...]              # net/terminal names, matrix order
    C: NDArray                              # (T, T) Maxwell capacitance, F
    G: NDArray | None                       # (F?, T, T) or (T, T) S
    freqs: Floats | None                    # for R(f), L(f)
    R: NDArray | None                       # (F, T, T) ohm (loop/port basis, see basis)
    L: NDArray | None                       # (F, T, T) H
    basis: Literal["port_loop", "partial"]
    validity_fmax: Frequency                # REQUIRED: ~ c / (10 * max_dim * sqrt(eps_eff))
    passivity: "MatrixPassivity"            # C diagonally dominant, R/L SPD; fixes applied
    spice: ArtifactRef | None = None        # subckt (pi sections / vector-fitted RL)

class FarFieldResult(OAModel):              # M2 (openEMS nf2ff), M3 (Palace/own transform)
    kind: Literal["farfield"] = "farfield"
    freqs: Floats; theta: Floats; phi: Floats
    e_theta: NDArray; e_phi: NDArray        # (F, T, P) complex, V at r = 1 m normalization
    p_rad: Floats; p_acc: Floats | None = None
    directivity_max: Floats; efficiency: Floats | None = None
```

### 4.2 Writers and readers *(WP-107, `oa_results.io`)*
- `write_bundle(bundle, dir)` and `read_bundle(dir)`. The on-disk layout is `dir/result.json` (the manifest, with arrays as `$array` refs) plus `dir/a/*.npy`, `dir/t/*.parquet` and `dir/x/*` (exports).
- `to_touchstone(net, path, version="2.0"|"1.1")` and `from_touchstone(path) -> NetworkResult` (unassessed; the caller gates it).
- `to_sarif(violations, board, path)` writes SARIF 2.1.0: `ruleId`, `level` mapped info→note, warning→warning, error→error, the location as a logical location (net/item) plus a physical location (board file, with the region giving XY in mm in the `properties` bag).
- `to_csv(table)`, `to_vtu(field, path)` (meshio), `to_html_report(bundle, path)` (M2, uses the static web build).

### 4.3 External execution

```python
class ExternalCommand(OAModel):
    program: str                            # logical tool: "openems-python", "palace", "fastercap",
                                            # "fasthenry", "kicad-cli", "kicad-python", "elmer"
    args: tuple[str, ...]
    workdir: Path                           # host path of the job directory (inputs already written)
    env: dict[str, str] = {}
    stdin: str | None = None
    timeout_s: float | None = None
    progress_parser: str | None = None      # registered parser id: "openems", "palace", "generic"
    outputs: tuple[str, ...] = ()           # relative paths that must exist after a successful run

class ExternalResult(OAModel):
    returncode: int
    stdout_tail: str; stderr_tail: str      # last 200 lines each (full logs in workdir/logs)
    wall_s: float
    runner: str; tool_version: str | None

@runtime_checkable
class ExternalRunner(Protocol):
    name: ClassVar[str]                     # "local", "wsl", "docker", "ssh-slurm"
    def available(self) -> bool: ...
    def resolve(self, program: str) -> "ToolInfo": ...   # path/image + version; raises ToolMissing
    def run(self, cmd: ExternalCommand, on_progress: Callable[[float, str], None],
            cancel: "CancelToken") -> ExternalResult: ...
    def translate_path(self, host_path: Path) -> str: ... # e.g. D:\x -> /mnt/d/x (wsl), /work (docker)
```

Tool configuration lives in the user-level `~/.config/open_analysis/tools.toml` (or `%APPDATA%\open_analysis\tools.toml`), overridable per project (`oa-project.yaml: tools:`):

```toml
[tools.kicad-cli]      runner = "local"; path = 'D:\Program Files\KiCad\10.0\bin\kicad-cli.exe'
[tools.kicad-python]   runner = "local"; path = 'D:\Program Files\KiCad\10.0\bin\python.exe'
[tools.openems-python] runner = "local"; path = 'C:\openEMS\python\python.exe'; min_version = "0.37"
[tools.palace]         runner = "wsl";   path = "/opt/palace/bin/palace"; mpi_ranks = 8
# or: runner = "docker"; image = "ghcr.io/awslabs/palace:0.18.1"
```

`oa doctor` lists each tool's resolution and version.

### 4.4 Job service *(W0 protocol in `oa_framework.jobs`; implementation `oa_jobs`, WP-107)*

```python
class AnalysisRequest(OAModel):
    project: Path                           # oa-project.yaml
    setup: str                              # setup name (file stem under oa/setups/) or inline:
    inline_setup: "AnalysisSetup | None" = None
    executor: Literal["inline", "process"] = "process"
    max_workers: int | None = None
    keep_scratch: bool = False
    priority: int = 0

JobState = Literal["queued", "planning", "running", "reducing", "succeeded",
                   "failed", "cancelled"]

class JobStatus(OAModel):
    id: str                                 # ULID
    state: JobState
    analysis: str; setup: str
    progress: float                         # 0..1 (weighted by task est_seconds)
    tasks_total: int; tasks_done: int; tasks_cached: int; tasks_failed: int
    message: str = ""
    result_id: str | None = None
    error: "ErrorInfo | None" = None
    created_utc: str; updated_utc: str

class JobEvent(OAModel):
    seq: int                                # monotonically increasing per job
    job_id: str
    type: Literal["state", "progress", "log", "task_started", "task_finished", "task_cached",
                  "diagnostic", "result"]
    task_id: str | None = None
    data: dict[str, JsonValue]
    utc: str

@runtime_checkable
class JobService(Protocol):
    def submit(self, req: AnalysisRequest) -> str: ...
    def status(self, job_id: str) -> JobStatus: ...
    def list(self, *, state: JobState | None = None, limit: int = 100) -> list[JobStatus]: ...
    def cancel(self, job_id: str) -> None: ...
    def events(self, job_id: str, after_seq: int = 0, *, follow: bool = False) -> Iterator[JobEvent]: ...
    def result(self, job_id: str) -> ResultBundle: ...
    def wait(self, job_id: str, timeout: float | None = None) -> JobStatus: ...
```

Implementation contract (`oa_jobs`):
- **State store.** SQLite at `<project>/.oa/jobs.sqlite` in WAL mode, with tables `jobs`, `tasks` and `events`. A job survives a server restart. Tasks that were running at restart go back to queued.
- **Worker executors.**
  - `inline` runs in the calling thread. It is used by tests and `oa ... --inline`.
  - `process` uses a pool of `spawn`ed worker processes (`oa_jobs.worker`). The default size is `min(cpu-1, 8)`.
  - gmsh runs only inside worker processes, never in the server process.
  - Workers receive `Task` JSON plus artifact paths, and they import the analysis by entry point.
- **Cache.** The task key is `sha256(analysis.kind, analysis.version, canonical_json(payload), sorted(input artifact sha256s), board_hash, physics_hash, tool versions for needs)`. Outputs are stored in `<project>/.oa/cache/tasks/<key>/output.json` plus artifacts in a shared CAS `<project>/.oa/cache/cas/<sha256[:2]>/<sha256>`. A hit emits a `task_cached` event. The `oa cache gc --max-size 20GB` command evicts by LRU.
- **Cancellation** is cooperative (`ctx.check_cancelled`). External processes are killed after a 10 s grace period. On Windows the whole process tree is killed via a job object.
- **Results** go to `<project>/.oa/results/<result_id>/` (section 4.2).

### 4.5 S-parameter quality gate *(WP-106, `oa_network.quality`)*

```python
class QualityCriteria(OAModel):
    passivity_tol: float = 1e-6             # max singular value <= 1 + tol counts as passive
    pqm_min: float = 99.0                   # IEEE 370 style: % ; >= 99.9 "good", >= 99 "acceptable"
    rqm_min: float = 99.0
    cqm_min: float = 80.0                   # causality metric threshold for "acceptable"
    require_dc: Literal["present", "extrapolatable", "none"] = "extrapolatable"
    max_df_fraction: float = 0.01           # frequency step <= 1% of f_max for causality evaluation

class QualityReport(OAModel):
    verdict: Literal["pass", "warn", "fail"]
    pqm: float; rqm: float; cqm: float      # percent, IEEE 370-2020 style definitions
    max_singular_value: float
    worst_passivity_freq: Frequency | None
    reciprocity_max_err: float
    dc: Literal["present", "extrapolated", "missing"]
    f_min: float; f_max: float; n_freqs: int
    criteria: QualityCriteria
    fixes_applied: tuple[str, ...] = ()     # e.g. "dc_extrapolated", "passivity_enforced(vf, 24 poles)"
    notes: tuple[str, ...] = ()

def assess(net: "skrf.Network", criteria: QualityCriteria = QualityCriteria()) -> QualityReport: ...
def make_dc(net, method: Literal["skrf", "rational"] = "skrf") -> "skrf.Network": ...
def enforce_passivity(net, *, n_poles: int | None = None) -> tuple["skrf.Network", "VFModel"]: ...
def gate(net, criteria, *, fix: bool = False) -> tuple["skrf.Network", QualityReport]: ...
```

Verdict rule:
- **fail** if `max_singular_value > 1 + 1e-3`, or `pqm < pqm_min`, or `rqm < rqm_min`, or the DC requirement is not met.
- **warn** if `cqm < cqm_min` or any fix was applied.
- **pass** otherwise.

A bundle that contains a network with verdict `fail` gets `status="failed_quality"`. Downstream time-domain consumers (M2 SI) must refuse such networks unless `allow_failed_quality: true` is set in their setup.

---

## 5. EM problem description (solver-neutral) *(W0 types, `oa_model.em`)*

### 5.1 Structure3D: shared by full-wave, quasi-static and 3D thermal

```python
# oa_model/em.py
class Box3(OAModel):
    xmin: float; ymin: float; zmin: float; xmax: float; ymax: float; zmax: float

class MaterialSlab(OAModel):
    """Dielectric layer clipped to the region: occupies [z_bottom, z_top] over `footprint`
    (default: the whole region box minus nothing)."""
    material: str                           # key into Structure3D.materials
    z_bottom: float; z_top: float
    footprint: Polygon | None = None        # None = full region rectangle; board outline ∩ box otherwise

class ConductorBody(OAModel):
    """Extruded copper: polygons (with holes, e.g. antipads) on one conductor layer."""
    id: str
    net: NetId | None
    role: Literal["signal", "reference", "floating"]
    layer: LayerId
    z_bottom: float; z_top: float
    polygons: tuple[Polygon, ...]
    material: str = "copper"
    roughness: Roughness = NoRoughness()
    representation: Literal["solid", "sheet", "auto"] = "auto"   # auto: sheet (zero-thickness at z_top
                                            # of the copper) if thickness < cell/elem limit; adapter decides

class ViaBody(OAModel):
    id: str
    net: NetId | None
    role: Literal["signal", "reference", "floating"]
    center: Vec2
    z_bottom: float; z_top: float           # plated span after backdrill
    r_outer: float                          # drill radius
    plating: float                          # thickness; >= r_outer means filled
    fill_material: str | None = None        # conductive fill or None
    pads: tuple["ViaPad", ...] = ()         # pad discs at layers (already included in ConductorBody
                                            # polygons; listed here for FDTD snapping hints)

class LumpedElement(OAModel):
    """Component placeholder between two terminals (SMD R/L/C). Series RLC; parallel uses topology."""
    id: str; refdes: str | None = None
    positive: "EMTerminalGeom"; negative: "EMTerminalGeom"
    r: float | None = None; l: float | None = None; c: float | None = None
    topology: Literal["series", "parallel"] = "series"

class Structure3D(OAModel):
    schema: Literal["oa-structure/1"] = "oa-structure/1"
    bbox: Box3                              # region of interest (copper + dielectric), board frame
    materials: tuple[Material, ...]
    slabs: tuple[MaterialSlab, ...]
    conductors: tuple[ConductorBody, ...]
    vias: tuple[ViaBody, ...]
    lumped: tuple[LumpedElement, ...] = ()
    background: str = "air"                 # material outside the board
    provenance: "RegionProvenance"          # board hash, RegionSpec, nets, bbox, cut rules applied
    def key(self) -> str: ...               # canonical sha256 (1 nm rounding), excludes provenance
```

### 5.2 EM ports and boundaries (resolved geometric forms)

```python
class EMTerminalGeom(OAModel):
    """A conductor contact as geometry: a small face/edge on a conductor surface."""
    position: Vec2; z: float
    layer: LayerId | None
    extent: tuple[float, float] = (0.0, 0.0)   # width along u, v of the contact patch

class EMLumpedPort(OAModel):
    kind: Literal["lumped"] = "lumped"
    name: str
    positive: EMTerminalGeom; negative: EMTerminalGeom
    width: float                            # current sheet width
    z0: float = 50.0
    excite: bool = True

class EMWavePort(OAModel):
    """Axis-aligned rectangular face on the region boundary. FDTD adapters may implement it as a
    TL port (MSL/stripline/CPW probes) if they lack modal ports; they must say so in capabilities."""
    kind: Literal["wave"] = "wave"
    name: str
    face: Literal["xmin", "xmax", "ymin", "ymax"]
    rect: tuple[float, float, float, float] # (u_min, u_max, z_min, z_max) on that face
    n_modes: int = 1
    line_type: Literal["microstrip", "stripline", "cpw", "cpwg", "general"] = "general"
    signal_conductors: tuple[str, ...]      # ConductorBody ids crossing the face
    z0: float | None = None                 # None: renormalize to modal impedance
    deembed: float = 0.0                    # reference plane shift into the structure (m)
    excite: bool = True

class EMCoaxPort(OAModel):
    kind: Literal["coax"] = "coax"
    name: str; center: Vec2; z: float; r_inner: float; r_outer: float
    dielectric: str; direction: Literal["+z", "-z"]; z0: float = 50.0; excite: bool = True

EMPort = Annotated[EMLumpedPort | EMWavePort | EMCoaxPort, Field(discriminator="kind")]

class Boundaries(OAModel):
    xmin: "BC"; xmax: "BC"; ymin: "BC"; ymax: "BC"; zmin: "BC"; zmax: "BC"
    air_padding: float | None = None        # extra air around bbox for open faces; auto = lambda(f_min)/4
BC = Literal["pec", "pmc", "absorbing", "pml"]   # adapters map absorbing->Mur/1st-order ABC, pml->UPML

class FrequencyPlan(OAModel):
    f_min: float = Field(ge=0)              # 0 allowed: adapter starts at its lowest valid point
    f_max: Frequency
    n_points: int = 401
    spacing: Literal["linear", "log"] = "linear"
    adaptive: bool = False                  # FEM fast sweep
    extra_points: tuple[Frequency, ...] = ()

class MeshSettings(OAModel):
    preset: Literal["coarse", "medium", "fine", "expert"] = "medium"
    cells_per_wavelength: float | None = None  # FDTD: coarse 10, medium 20, fine 30
    max_grading: float = 1.3
    min_cell: float | None = None
    fem_order: int = 2
    amr_max_passes: int = 0                 # FEM: 0 = none
    expert: dict[str, JsonValue] = {}       # adapter-specific overrides, namespaced: {"openems.x": ...}

class Convergence(OAModel):
    energy_decay_db: float = -50.0          # FDTD end criterion
    max_timesteps: int = 1_000_000
    delta_s: float = 0.02                   # FEM AMR target |ΔS|
    max_wall_s: float | None = None

class OutputRequest(OAModel):
    sparams: bool = True
    farfield: "FarFieldRequest | None" = None
    field_dumps: tuple["FieldDumpRequest", ...] = ()   # planes or volumes at frequencies (bounded size)
    port_impedance: bool = True             # modal Z0 per port vs frequency

class FullWaveDriven(OAModel):
    kind: Literal["fullwave_driven"] = "fullwave_driven"
class Eigenmode(OAModel):                   # M3
    kind: Literal["eigenmode"] = "eigenmode"; n_modes: int = 10; f_target: Frequency
class Electrostatic(OAModel):               # quasi-static C (Palace/FasterCap/Elmer), M2
    kind: Literal["electrostatic"] = "electrostatic"
class Magnetostatic(OAModel):               # DC L (Palace); FastHenry uses QSProblem below
    kind: Literal["magnetostatic"] = "magnetostatic"
Physics = Annotated[FullWaveDriven | Eigenmode | Electrostatic | Magnetostatic, Field(discriminator="kind")]

class EMProblem(OAModel):
    schema: Literal["oa-emproblem/1"] = "oa-emproblem/1"
    name: str
    structure: Structure3D
    physics: Physics = FullWaveDriven()
    ports: tuple[EMPort, ...]
    boundaries: Boundaries
    frequency: FrequencyPlan
    mesh: MeshSettings = MeshSettings()
    convergence: Convergence = Convergence()
    outputs: OutputRequest = OutputRequest()
    def key(self) -> str: ...               # sha256 of canonical form excluding `name`
```

### 5.3 Solver adapter interface *(W0 protocol, `oa_framework.em`)*

```python
class AdapterCapabilities(OAModel):
    physics: frozenset[str]                 # {"fullwave_driven"} ...
    ports: frozenset[str]                   # {"lumped", "wave", "coax"}
    wave_port_mode: Literal["modal", "tl_probe", "none"]
    boundaries: frozenset[str]
    dispersive_dielectric: Literal["broadband", "single_frequency_fit", "none"]
    surface_roughness: bool
    sheet_conductors: bool
    excitations_per_run: Literal["one", "all"]    # openEMS: one; Palace: all
    farfield: bool
    platforms: frozenset[str]               # {"windows", "linux", "macos"}

class PreparedRun(OAModel):
    workdir: Path
    commands: tuple[ExternalCommand, ...]   # one per excitation when excitations_per_run == "one"
    mesh_stats: dict[str, JsonValue]        # cells/DOF, min cell, est memory (GB), est time
    files: tuple[str, ...]

class EMSolveOutput(OAModel):
    network: NetworkResult | None           # quality NOT yet assessed: quality filled by oa_em
    port_impedance: TableResult | None
    farfield: FarFieldResult | None
    fields: tuple[ArtifactRef, ...] = ()
    convergence_log: TableResult | None
    solver_version: str
    warnings: tuple[Diagnostic, ...] = ()

@runtime_checkable
class SolverAdapter(Protocol):
    name: ClassVar[str]                     # "openems", "palace", "emerge"
    tool: ClassVar[str]                     # ExternalCommand.program used
    def capabilities(self) -> AdapterCapabilities: ...
    def check(self, problem: EMProblem) -> tuple[Diagnostic, ...]: ...   # unsupported features -> error
    def prepare(self, problem: EMProblem, workdir: Path, tool: "ToolInfo") -> PreparedRun: ...
    def collect(self, problem: EMProblem, run: PreparedRun,
                results: Sequence[ExternalResult]) -> EMSolveOutput: ...
```

Flow inside `oa_em` (`kind="em.sparams"`):
1. `plan()` turns the setup into a `RegionSpec`, then `geometry.cut_region()`, then resolves ports (`TraceEndPort`→`EMWavePort`, `LumpedPort`→`EMLumpedPort`) and builds the `EMProblem`.
2. It runs `adapter.check()` and then creates tasks: one `prepare` task, N `run` tasks (one per command), and a `collect` task.
3. `reduce()` calls `oa_network.quality.gate(fix=setup.fix_quality)` and then writes the Touchstone file and a JSON sidecar (region, port map, mesh stats, convergence, quality).

Adapters must be testable without the solver: `prepare()` is pure file generation, so it gets snapshot tests, and `collect()` parses committed sample outputs.

### 5.4 Quasi-static extraction (M2) *(types reserved in W0, `oa_model.qs`)*

```python
class QSTerminal(OAModel):
    name: str; net: NetId
    source: EMTerminalGeom | None = None    # for R/L: current injection face
    sink: EMTerminalGeom | None = None      # for R/L: current return face

class QSProblem(OAModel):
    schema: Literal["oa-qsproblem/1"] = "oa-qsproblem/1"
    structure: Structure3D
    terminals: tuple[QSTerminal, ...]
    want: frozenset[Literal["C", "G", "R", "L"]]
    freqs: Floats | None = None             # for R(f), L(f)
    ground_net: NetId | None = None
    discretization: dict[str, JsonValue] = {}

@runtime_checkable
class ExtractionAdapter(Protocol):          # FasterCap, FastHenry2, Palace ES/MS, Elmer
    name: ClassVar[str]; tool: ClassVar[str]
    provides: ClassVar[frozenset[str]]      # {"C"} for FasterCap, {"R","L"} for FastHenry
    def check(self, p: QSProblem) -> tuple[Diagnostic, ...]: ...
    def prepare(self, p: QSProblem, workdir: Path, tool: "ToolInfo") -> PreparedRun: ...
    def collect(self, p: QSProblem, run: PreparedRun, results: Sequence[ExternalResult]) -> ExtractionResult: ...
```

`oa_qs` (M2) merges adapter outputs per `want`, enforces matrix passivity, and emits a SPICE subcircuit made of π sections, with vector-fitted RL ladders for R(f)/L(f) (report 07 §1.5). `validity_fmax` is always set.

### 5.5 Thermal problem (M2) *(types reserved in W0, `oa_model.thermal`)*

```python
class HeatSource(OAModel):
    id: str
    kind: Literal["component", "polygon", "joule_field"]
    component: str | None = None            # refdes: power over courtyard (or pads)
    polygon: Polygon | None = None; layer: LayerId | None = None
    power: float = 0.0                      # W
    theta_jc: float | None = None; theta_jb: float | None = None   # K/W two-resistor model (JESD15-3)

class ThermalBC(OAModel):
    t_ambient: float = 298.15               # K
    h_top: float = 10.0; h_bottom: float = 10.0       # W/(m^2 K) convection
    emissivity_top: float | None = 0.9; emissivity_bottom: float | None = 0.9
    fixed_temperature: tuple[tuple[Polygon, float], ...] = ()     # heatsink / cold plate (K)

class ThermalProblem(OAModel):
    schema: Literal["oa-thermalproblem/1"] = "oa-thermalproblem/1"
    grid: Literal["layered_fvm", "fem3d"] = "layered_fvm"
    cell_size: float = 0.25e-3
    sources: tuple[HeatSource, ...]
    bc: ThermalBC
    structure: Structure3D | None = None    # fem3d (Elmer) only; layered_fvm reads the Board directly
```

Electro-thermal coupling (M2) is a Picard loop owned by `oa_thermal`:
1. Ask the `pdn` service for a DC solve at temperature field T. The service returns the Joule density `q` on the DC triangulation.
2. Integrate `q` conservatively onto the thermal raster cells.
3. Solve T and interpolate it back to the triangles, which gives σ(T).
4. Repeat until max|ΔT| < 0.1 K, with under-relaxation 0.7 and at most 20 iterations.
5. If it does not converge, report "possible thermal runaway" as an error. Never hide it.

---

## 6. Setup and project files *(W0 schema, `oa_model.setup`)*

```
myboard/
  myboard.kicad_pcb, myboard.kicad_pro
  oa-project.yaml            # manifest (below)
  myboard.oa-physics.yaml    # physics sidecar (board-model.md §11)
  oa/intent.yaml             # DesignIntent (board-model.md §12.1)
  oa/setups/*.yaml           # one AnalysisSetup per file, versioned in git
  .oa/                       # cache, jobs.sqlite, results (git-ignored)
```

```python
class ProjectManifest(OAModel):
    schema: Literal["oa-project/1"]
    name: str
    board: Path                             # relative: myboard.kicad_pcb | .xml | .PcbDoc | .oab(.json)
    physics: Path | None = None
    intent: Path | None = None
    setups_dir: Path = Path("oa/setups")
    read_options: ReadOptions = ReadOptions()
    tools: dict[str, dict[str, str]] = {}   # per-project tool overrides

class AnalysisSetup(OAModel):
    schema: Literal["oa-setup/1"]
    name: str                               # unique in project; defaults to file stem
    analysis: str                           # Analysis.kind
    description: str = ""
    config: dict[str, JsonValue]            # validated against Analysis.Config at load
    ports: tuple[Port, ...] = ()            # EM / PI
    waivers: tuple[Waiver, ...] = ()        # ERC
```

Example `oa/setups/via-sparams.yaml`:

```yaml
schema: oa-setup/1
analysis: em.sparams
config:
  solver: openems
  region: {nets: [PCIE_TX0_P, PCIE_TX0_N], margin: 2mm}
  frequency: {f_min: 0, f_max: 40GHz, n_points: 801}
  mesh: {preset: medium}
  fix_quality: true
ports:
  - {kind: trace_end, name: P1, nets: [PCIE_TX0_P, PCIE_TX0_N], side: xmin, modes: differential}
  - {kind: trace_end, name: P2, nets: [PCIE_TX0_P, PCIE_TX0_N], side: xmax, modes: differential}
```

Example `oa/setups/dcir-1v0.yaml` (`pi.dc` Config, WP-204):

```yaml
schema: oa-setup/1
analysis: pi.dc
config:
  rail: {net: +1V0, return: GND}
  vrm: {component: U5, pins: ["9", "10", "11"], vset: 1.0, sense: {pos: U1.AA12, neg: U1.AB12}}
  loads:
    - {component: U1, nets: [+1V0], current: 12A, model: pin_group}
  temperature: 25degC
  mesh: {h_plane: 0.8mm, h_trace_factor: 0.33, via_ring_segments: 16}
  limits: {drop_fraction: 0.03, via_current_density: 3e8}   # A/m^2
```

---

## 7. REST API outline (`oa_server`, WP-207; OpenAPI committed at `web/src/api/openapi.json`)

Base `http://127.0.0.1:<port>/api/v1`. Auth uses a random bearer token printed by `oa serve` and passed to the browser in the URL fragment. Every request needs `Authorization: Bearer <t>`. The server binds to 127.0.0.1 only unless `--host` is given (with a warning). JSON uses SI units. Binary geometry uses Apache Arrow IPC streams.

| Method | Path | Request / response |
|---|---|---|
| GET | `/health` | `{version, tools: [{name, available, version}]}` |
| GET | `/projects` | `[ProjectSummary]` (recently opened) |
| POST | `/projects/open` | `{path}` → `ProjectSummary{id, name, board_hash, diagnostics}` |
| POST | `/projects/{pid}/reload` | re-ingest; → `ProjectSummary` |
| GET | `/projects/{pid}/board` | `BoardSummary{meta, stackup, nets(count), layers, components(count), diagnostics}` |
| GET | `/projects/{pid}/board/stackup` | `Stackup` (board-model JSON) |
| GET | `/projects/{pid}/board/nets?query=&kind=&limit=` | `[NetInfo{id, kind, net_class, n_items, length, layers}]` |
| GET | `/projects/{pid}/board/layers/{layer}/geometry?nets=&lod=` | Arrow IPC: polygons (`net_idx`, `item_idx`, flattened ring offsets, float32 xy in **mm**) for deck.gl `SolidPolygonLayer` binary attributes |
| GET | `/projects/{pid}/board/layers/{layer}/tracks` | Arrow IPC: segments (x0,y0,x1,y1,width,net_idx,item_idx), arcs pre-tessellated |
| GET | `/projects/{pid}/board/drills` | Arrow IPC: (x, y, d, kind, net_idx) |
| GET | `/projects/{pid}/board/items/{item_id}` | item JSON (track/via/pad/zone) + `source` ref |
| GET | `/projects/{pid}/board/structure3d?bbox=` | glTF binary (GLB) of the extruded region for three.js |
| GET | `/analyses` | `[AnalysisInfo{kind, title, version, config_schema (JSON Schema), port_kinds}]` |
| GET | `/rules` | `[RuleInfo{id, title, category, aliases, default_severity, config_schema, references}]` |
| GET/PUT/DELETE | `/projects/{pid}/setups/{name}` | `AnalysisSetup` (PUT validates against the config schema; writes YAML) |
| GET | `/projects/{pid}/setups` | `[SetupSummary]` |
| POST | `/projects/{pid}/setups/{name}/validate` | `[Diagnostic]` |
| POST | `/projects/{pid}/jobs` | `{setup, executor?}` → `JobStatus` |
| GET | `/projects/{pid}/jobs?state=` | `[JobStatus]` |
| GET | `/jobs/{jid}` | `JobStatus` |
| POST | `/jobs/{jid}/cancel` | `JobStatus` |
| GET | `/results/{rid}` | `ResultBundle` manifest (arrays as `$array` refs) |
| GET | `/results/{rid}/items/{item}/arrays/{name}` | `application/x-npy` or Arrow |
| GET | `/results/{rid}/items/{item}/table?offset=&limit=&sort=` | Arrow IPC |
| GET | `/results/{rid}/export/{format}` | `touchstone` \| `sarif` \| `csv` \| `vtu` \| `html` (file download) |
| WS | `/events?token=` | server → client `JobEvent` JSON frames; client → server `{subscribe: [jid,...]}` |

Error body (every 4xx/5xx): `{"error": {"code": "SETUP_INVALID", "message": "...", "details": [...]}}`. Codes come from section 12.

---

## 8. CLI outline (`oa_cli`, WP-207, typer)

```
oa ingest BOARD [--out board.oab] [--refill-zones] [--format json|summary]
oa doctor
oa init [DIR]                                   # writes oa-project.yaml, physics + intent templates
oa erc [PROJECT] [--setup erc] [--sarif out.sarif] [--fail-on error|warning|never]
oa impedance xs FILE.yaml|--microstrip w=.. h=.. t=.. er=.. [--method auto|closed_form|bem|fem]
oa impedance profile [PROJECT] --net NAME [--csv out.csv]
oa pi dc [PROJECT] --setup NAME [--vtu out/]
oa em [PROJECT] --setup NAME [--solver openems|palace] [--touchstone out.s4p]
oa run [PROJECT] --setup NAME                   # any analysis
oa jobs list|status|cancel|logs JOB
oa results show|export RESULT --format ...
oa serve [PROJECT] [--port 0] [--no-browser]
oa cache gc [--max-size 20GB]
```

Exit codes:
- 0: ok.
- 1: analysis completed, but violations or failures reached the `--fail-on` level.
- 2: usage or setup error.
- 3: missing tool.
- 4: internal error.
- 5: quality gate failed.

---

## 9. Mesh contracts (`oa_mesh`, WP-109 2D, WP-210 3D)

```python
def mesh_cross_section(xs: CrossSection, *, h_max: float | None = None, order: int = 2,
                       corner_refine: float = 0.1, box_factor: float = 20.0) -> "XsMesh": ...
# XsMesh: nodes (N,2), triangles (M,3 or 6 for P2), region tag per triangle (dielectric index or -1 air),
#         boundary edge tags per conductor id, outer boundary tag.

def mesh_copper_sheets(pieces: Sequence["SheetPiece"], *, h_default: float, size_fields: Sequence["SizeField"]
                       ) -> tuple[TriMesh2D, ...]: ...
# SheetPiece: (layer, net, shapely-free Polygon list, embedded points/rings for via & pad contacts)
# Output nodes include every contact-ring node; mapping contact_id -> node indices returned in .contacts

def build_occ_model(structure: Structure3D, *, sheet_threshold: float, ports: Sequence[EMPort]
                    ) -> "OccModel": ...          # WP-210: BooleanFragments, physical groups
def write_msh(model: "OccModel", path: Path, *, sizes: MeshSettings) -> "MeshStats": ...
```

gmsh is initialized and finalized per call inside `oa_mesh._session()`, which is not re-entrant. Callers must only run in worker processes. Mesh outputs are deterministic for a given gmsh version (`General.RandomSeed` fixed, `Mesh.Algorithm=6`, and 3D `Mesh.Algorithm3D=10`).

---

## 10. Physics functions (`oa_physics`, WP-106)

```python
def eps_complex(model: DielectricModel, freqs: ArrayLike) -> np.ndarray      # eps' - j eps''
def ds_parameters(m: DjordjevicSarkar) -> tuple[float, float, float, float]  # eps_inf, d_eps, m1, m2
def fit_ds(freqs, dk, df, *, m1=4.0, m2=12.0) -> DjordjevicSarkar          # least squares
def as_causal(model: DielectricModel) -> DielectricModel                     # Constant -> DS
def surface_impedance(sigma: float, freqs, roughness: Roughness, *, causal: bool = True) -> np.ndarray
def roughness_factor(roughness: Roughness, freqs, sigma: float) -> np.ndarray  # K(f) >= 1
def skin_depth(sigma: float, freqs, mu_r: float = 1.0) -> np.ndarray
def resistivity(m: ConductorMaterial, T: float | np.ndarray) -> np.ndarray
def sheet_resistance(m: ConductorMaterial, thickness: float, T: float) -> float
def etch_widths(w_drawn: float, thickness: float, etch: Etch | None) -> tuple[float, float]  # (w_bottom, w_top)
def kk_residual(freqs, eps: np.ndarray) -> float                              # causality check helper
```

---

## 11. Analysis kinds and their Configs (owners)

| kind | Owner WP | Config essentials | Result items |
|---|---|---|---|
| `impedance.xsection` | WP-105 | `xsection` (inline CrossSection or a geometry sample ref), `freqs`, `method` | `rlgc`, `scalars` |
| `impedance.profile` | WP-203 | `nets: NetSelector`, `ds`, `freqs`, `method`, `tolerance` | `profile` per net, `table` |
| `erc` | WP-201 | `rules: {RULE_ID: {enabled, severity?, ...cfg}}`, `nets: NetSelector?` | `violations`, `table` |
| `pi.dc` | WP-204 | see §6 example | `field` (V, dV, J per layer), `table` (loads, vias), `scalars` |
| `em.sparams` | WP-205 | `solver`, `region`, `frequency`, `mesh`, `convergence`, `fix_quality` | `network`, `table` (port Z), `artifact` (mesh preview) |
| `qs.extract` | WP-M2 | `region`, `want`, `backends` | `extraction` |
| `thermal.board` | WP-M2 | ThermalProblem + `electrothermal: {rail setups}` | `field` (T), `table` (Tj) |

---

## 12. Errors and diagnostics

```python
class OAError(Exception):
    code: str                                     # stable machine code
class IngestError(OAError): ...                   # INGEST_*
class BoardValidationError(OAError): ...          # MODEL_INVALID
class UnsupportedSchemaVersion(OAError): ...      # SCHEMA_VERSION
class SetupError(OAError): ...                    # SETUP_INVALID (carries Diagnostics)
class ToolMissing(OAError): ...                   # TOOL_MISSING (program, hint)
class ExternalToolFailed(OAError): ...            # TOOL_FAILED (returncode, log tail)
class Cancelled(OAError): ...                     # CANCELLED
class QualityGateMissing(OAError): ...            # QUALITY_GATE_MISSING
class UnsupportedFeature(OAError): ...            # adapter cannot represent a problem feature
```

Logging uses stdlib `logging` with structured `extra` fields. The logger names match the packages (`oa.ingest.kicad` and so on). Nothing prints to stdout except the CLI.
