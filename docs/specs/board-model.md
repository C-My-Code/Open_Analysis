# Board Model v1 (FROZEN)

Status: **FROZEN v1.0 (commit 6b4d083)**, the merge of WP-000A/B/C. Accepted additive changes: docs/change-requests/WP-000B-*.md and the addendum in ai-integration.md §13. Any further change needs an ADR and must follow the versioning policy in section 14.
Package: `packages/oa-model` (import name `oa_model`). Owner: WP-000 until it merges, then the architecture group (changes go through ADRs).
Related: [interfaces.md](interfaces.md) (CrossSection, EMProblem, results, plugins), [ADR-0003](../adr/0003-board-model-format.md).

This document defines the neutral, tool-independent description of a printed circuit board that every Open_Analysis package consumes. Ingest adapters (KiCad, IPC-2581, Altium via kicad-cli) produce it. Geometry, solvers, rules and the UI read it. Nothing in it is KiCad-specific apart from `source_*` alias fields.

---

## 1. Implementation choice: pydantic v2 frozen models

We use **pydantic v2 `BaseModel` with `frozen=True`**, not frozen dataclasses.

Why:
1. **JSON Schema for free.** `Board.model_json_schema()` produces the published schema (`schemas/board-model.v1.json`). The web UI generates TypeScript types from it, the REST API (FastAPI) uses the same classes, and analysis `Config` classes produce UI forms the same way. With dataclasses we would maintain the schema by hand.
2. **Validation at trust boundaries.** Board JSON, physics sidecars and setup YAML are user- or machine-written files. Pydantic gives typed errors with paths.
3. **Discriminated unions** (`kind` fields) for pad shapes, dielectric models, roughness models, track segments and ports. These are first-class in pydantic and in JSON Schema.
4. **Speed is adequate.** pydantic-core is compiled. Readers that build hundreds of thousands of items may use `model_construct()` (no validation) on the hot path and then run `validate_board()` once (section 13). Bulk coordinates go into a custom immutable `Points` array type, not lists of tuples, so a 100 MB board does not become 10^7 Python floats.

The rules every model in `oa_model` follows:

```python
# oa_model/_base.py
from pydantic import BaseModel, ConfigDict


class OAModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,  # immutable after construction
        extra="forbid",  # typos and unknown fields are errors (see section 14 for newer-minor files)
        allow_inf_nan=False,  # no NaN/Inf anywhere in the model
        populate_by_name=True,
        use_enum_values=False,
        validate_default=True,
    )
```

- Collections are `tuple[...]`, never `list` or `dict`, so models are deeply immutable. Lookups by id go through `BoardIndex` (section 13).
- No shapely objects, no gmsh objects, no numpy arrays except inside `Points`/`Floats`. Derived geometry (unions, STRtrees, reference maps) belongs to `oa_geometry`.
- `oa_model` depends only on `pydantic>=2.7`, `numpy>=1.26`, and `pyyaml` (for the sidecar and setup loaders). It must import in under 200 ms.

---

## 2. Units and coordinate convention

| Quantity | Unit | Notes |
|---|---|---|
| Length, position | metre (m), float64 | Readers convert at the boundary: KiCad mm × 1e-3, Altium 1e-4 mil × 2.54e-9, IPC-2581 per its `units` attribute |
| Angle | radian, float64 | Counter-clockwise positive, measured from +X |
| Frequency | hertz (Hz) | |
| Conductivity | S/m | |
| Resistivity | Ω·m | |
| Temperature | kelvin (K) in the model | YAML may say `25degC`; the loader converts |
| Thermal conductivity | W/(m·K) | |
| Current, voltage, power | A, V, W | |
| Time | s | |

Coordinate frame (right-handed):
- **X** right, **Y up**, as seen from the **top** of the board. KiCad's Y-down coordinates are flipped by the reader (`y_oa = -y_kicad`). The origin is the source file's origin (no recentering), so coordinates can be cross-probed back into the source tool.
- **Z up**. `z = 0` is the **bottom surface of the bottom-most stack layer** (the bottom solder mask if present, otherwise the bottom copper). The top surface of the top layer is `z = Stackup.total_thickness`.
- Bottom-side items keep their true board-frame XY coordinates. Mirroring has already been applied by the reader. Nothing in the model is in a footprint-local frame except pad shape definitions, which are in the **pad-local** frame (section 8).
- Polygon rings: **exterior counter-clockwise, holes clockwise**. The ring is not closed (the first point is not repeated). There are no self-intersections, and holes lie strictly inside their exterior. Readers normalize this (KiCad "fractured" polygons are re-unioned to recover real holes).

Unit helpers live in `oa_model.units`:

```python
# oa_model/units.py
M = 1.0
MM = 1e-3
UM = 1e-6
NM = 1e-9
MIL = 25.4e-6
INCH = 25.4e-3
OZ_CU = 34.79e-6  # 1 oz/ft^2 copper thickness
HZ = 1.0
KHZ = 1e3
MHZ = 1e6
GHZ = 1e9
DEG = 3.141592653589793 / 180.0
ZERO_C = 273.15


def parse_quantity(text: str | float, dimension: str) -> float:
    """Parse '0.2mm', '5mil', '1.6 GHz', '3%', '25degC', '0.5mOhm' into SI floats.
    dimension in {"length","frequency","resistance","inductance","capacitance","current",
                  "voltage","temperature","time","fraction","conductivity","power"}.
    A bare number is taken as already SI. 'm' alone always means metre; milli needs a unit
    after it (mm, mOhm, mA). A dimension mismatch raises ValueError."""
```

Geometric tolerance: `oa_model.GEOM_EPS = 1e-9` m (1 nm, KiCad's internal resolution). Two points closer than this count as equal.

---

## 3. Array primitives

```python
# oa_model/arrays.py
from __future__ import annotations
import hashlib
import numpy as np
from pydantic_core import core_schema


class Points:
    """Immutable (N, 2) float64 array of XY coordinates in metres.

    JSON form: a flat list [x0, y0, x1, y1, ...] (even length), or {"$array": "<ref>"}
    inside a packed .oab container (section 14.3).
    """

    __slots__ = ("_a",)

    def __init__(self, data) -> None:
        a = np.array(data, dtype=np.float64, copy=True)
        if a.ndim == 1:
            if a.size % 2:
                raise ValueError("flat point list must have even length")
            a = a.reshape(-1, 2)
        if a.ndim != 2 or a.shape[1] != 2:
            raise ValueError(f"Points must be (N,2), got {a.shape}")
        if not np.isfinite(a).all():
            raise ValueError("Points contain NaN/Inf")
        a.setflags(write=False)
        self._a = a

    @property
    def array(self) -> np.ndarray:  # read-only view
        return self._a

    def __len__(self) -> int:
        return self._a.shape[0]

    def __eq__(self, other) -> bool:
        return isinstance(other, Points) and np.array_equal(self._a, other._a)

    def __hash__(self) -> int:
        return hash(self._a.tobytes())

    def digest(self) -> bytes:  # used by canonical hashing
        return hashlib.sha256(np.ascontiguousarray(self._a, dtype="<f8").tobytes()).digest()

    def to_flat_list(self) -> list[float]:
        return self._a.reshape(-1).tolist()

    @classmethod
    def __get_pydantic_core_schema__(cls, source, handler):
        def validate(v):
            return v if isinstance(v, Points) else Points(v)

        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda p: p.to_flat_list(), when_used="json"
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(cls, cs, handler):
        return {
            "type": "array",
            "items": {"type": "number"},
            "description": "Flat XY list in metres: [x0,y0,x1,y1,...]",
        }


class Floats:
    """Immutable 1-D float64 array (frequency tables, Dk/Df tables). Same contract as Points;
    JSON form is a plain list of numbers."""

    # identical pattern: shape (N,), finite, read-only, __eq__/__hash__/digest/to_list
```

`Vec2 = tuple[float, float]` is used for single points.

`NDArray` is the general immutable N-D array type, used by result containers and the RLGC matrices in interfaces.md. Its dtype is `f8`, `c16` or `i8`. Its JSON form is `{"dtype", "shape", "data"}`, with complex data interleaved as re, im. It follows the same `__eq__`/`__hash__`/`digest` contract as `Points`. `Board` itself does not use it.

---

## 4. Common types

```python
# oa_model/common.py
from typing import Annotated, Literal
from pydantic import Field, StringConstraints

Length = Annotated[float, Field(ge=0.0)]  # metres, non-negative
Coord = float  # metres, signed
Angle = float  # radians
Frequency = Annotated[float, Field(gt=0.0)]  # hertz
Vec2 = tuple[float, float]

ItemId = Annotated[str, StringConstraints(min_length=1, max_length=200)]
NetId = Annotated[str, StringConstraints(min_length=1)]  # = net name (unique per board)
LayerId = Annotated[str, StringConstraints(pattern=r"^(L[1-9][0-9]*|D[1-9][0-9]*|MT|MB)$")]
# L1..Ln : copper layers, top (L1) to bottom (Ln)
# D1..Dm : dielectric slabs, top to bottom (after sub-ply flattening)
# MT, MB : top / bottom solder mask
Side = Literal["top", "bottom"]


class SourceRef(OAModel):
    """Back-reference to the originating item, for cross-probing and diagnostics."""

    tool: Literal["kicad", "ipc2581", "altium", "odbpp", "synthetic", "user"]
    uuid: str | None = None  # KiCad uuid, IPC-2581 id, etc.
    token: str | None = None  # e.g. "segment", "zone", "via", "pad"
    name: str | None = None  # original layer/net/item name if useful


class Diagnostic(OAModel):
    severity: Literal["info", "warning", "error"]
    code: str  # e.g. "INGEST.ZONE_UNFILLED", "MODEL.DANGLING_NET_REF"
    message: str
    item: ItemId | None = None
    location: Vec2 | None = None
    layer: LayerId | None = None
```

Item id rule: readers set `id` to a stable string. They use the source uuid when there is one (`"k:" + uuid` for KiCad, `"i:" + id` for IPC-2581). Otherwise they use a deterministic hash `"h:" + sha1(kind, layer, net, coords)[:16]`. Ids must be unique across the whole board and stable across re-reads of an unchanged file.

---

## 5. Geometry primitives

```python
# oa_model/geometry.py
class Polygon(OAModel):
    """Simple polygon with holes. Discretized (no analytic arcs). Exterior CCW, holes CW."""

    exterior: Points
    holes: tuple[Points, ...] = ()


class LineSeg(OAModel):
    kind: Literal["line"] = "line"
    start: Vec2
    end: Vec2


class ArcSeg(OAModel):
    """Circular arc. Readers convert KiCad's 3-point (start, mid, end) arcs to this form."""

    kind: Literal["arc"] = "arc"
    start: Vec2
    end: Vec2
    center: Vec2
    ccw: bool  # direction of travel start -> end
    # radius = |start - center| (validator checks |end - center| matches within 1e-9 m)


PathSeg = Annotated[LineSeg | ArcSeg, Field(discriminator="kind")]
```

Arc policy: **centrelines (tracks) and board-outline edges keep analytic arcs** (`ArcSeg`), because impedance and length calculations need them exact. **Filled copper polygons are discretized polylines.** KiCad already stores zone fills that way. Readers discretize other arcs (custom pad primitives, outline arcs used to build `BoardOutline.polygon`) with a maximum sagitta error of `BoardMeta.arc_tolerance` (default 1 µm).

---

## 6. Stackup and materials

### 6.1 Materials (frequency-dependent, thermal-ready)

```python
# oa_model/materials.py

# ---- dielectric frequency models ----
class ConstantDkDf(OAModel):
    """Constant Dk/Df at f_ref. NOT causal; physics converts it to Djordjevic-Sarkar for any
    wideband or time-domain use (oa_physics.as_causal)."""

    kind: Literal["constant"] = "constant"
    dk: float = Field(ge=1.0)
    df: float = Field(ge=0.0, lt=1.0)
    f_ref: Frequency = 1e9


class DjordjevicSarkar(OAModel):
    """Wideband Debye. Defined by a single datasheet point. The eps_inf / delta_eps parameters are
    derived by oa_physics (documented formula in docs/research/02-impedance.md sec 2.4)."""

    kind: Literal["djordjevic_sarkar"] = "djordjevic_sarkar"
    dk: float = Field(ge=1.0)
    df: float = Field(ge=0.0, lt=1.0)
    f_ref: Frequency = 1e9
    f_low: Frequency = 1e4 / 6.283185307179586  # w1 = 10^m1 rad/s with m1 = 4
    f_high: Frequency = 1e12 / 6.283185307179586  # w2 = 10^m2 rad/s with m2 = 12
    # m1 = log10(2*pi*f_low), m2 = log10(2*pi*f_high). oa_physics must use exactly these.


class MultiPoleDebye(OAModel):
    kind: Literal["multipole_debye"] = "multipole_debye"
    eps_inf: float = Field(ge=1.0)
    poles: tuple[tuple[float, Frequency], ...]  # (delta_eps_k, f_relax_k)
    sigma_dc: float = Field(default=0.0, ge=0.0)  # S/m


class TableDkDf(OAModel):
    """Measured/vendor table. oa_physics interpolates log-f, linear Dk/Df, and offers a causal
    DS or multipole fit to it on request."""

    kind: Literal["table"] = "table"
    freqs: Floats  # strictly increasing, Hz
    dk: Floats
    df: Floats


DielectricModel = Annotated[
    ConstantDkDf | DjordjevicSarkar | MultiPoleDebye | TableDkDf, Field(discriminator="kind")
]


class ThermalProps(OAModel):
    """Bulk thermal properties. Anisotropic: in-plane (xy) and through-plane (z)."""

    k_xy: float = Field(gt=0.0)  # W/(m K)
    k_z: float = Field(gt=0.0)
    density: float | None = Field(default=None, gt=0.0)  # kg/m^3 (transient thermal)
    specific_heat: float | None = Field(default=None, gt=0.0)  # J/(kg K)
    emissivity: float | None = Field(default=None, ge=0.0, le=1.0)  # surfaces (mask)


class DielectricMaterial(OAModel):
    kind: Literal["dielectric"] = "dielectric"
    name: str  # library key, e.g. "Isola_370HR_2116", "FR4_generic"
    model: DielectricModel
    dk_z_over_dk_xy: float = Field(default=1.0, gt=0.0)  # anisotropy; 1.0 = isotropic
    thermal: ThermalProps | None = None
    source: Literal["ingest", "sidecar", "library", "default"] = "ingest"


class ConductorMaterial(OAModel):
    kind: Literal["conductor"] = "conductor"
    name: str  # e.g. "copper_ed", "copper_rolled"
    conductivity: float = Field(default=5.8e7, gt=0.0)  # S/m at t_ref
    t_ref: float = 293.15  # K
    temp_coeff: float = 3.93e-3  # 1/K, for rho(T) = rho0 (1 + a (T - t_ref))
    mu_r: float = Field(default=1.0, gt=0.0)
    thermal: ThermalProps | None = ThermalProps(k_xy=390.0, k_z=390.0)
    source: Literal["ingest", "sidecar", "library", "default"] = "default"


Material = Annotated[DielectricMaterial | ConductorMaterial, Field(discriminator="kind")]


# ---- conductor surface roughness ----
class NoRoughness(OAModel):
    kind: Literal["none"] = "none"


class Hammerstad(OAModel):
    kind: Literal["hammerstad"] = "hammerstad"
    rq: Length  # RMS roughness, m


class Groisse(OAModel):
    kind: Literal["groisse"] = "groisse"
    rq: Length


class Huray(OAModel):
    kind: Literal["huray"] = "huray"
    sphere_radius: Length  # a
    surface_ratio: float = Field(gt=0.0)  # N * 4 pi a^2 / A_flat (sum over one size class)
    flat_ratio: float = Field(default=1.0, gt=0.0)  # A_matte / A_flat


class CannonballHuray(OAModel):
    kind: Literal["cannonball_huray"] = "cannonball_huray"
    rz: Length  # datasheet Rz (10-point height)


Roughness = Annotated[
    NoRoughness | Hammerstad | Groisse | Huray | CannonballHuray, Field(discriminator="kind")
]
```

### 6.2 Stack layers

```python
# oa_model/stackup.py
class Etch(OAModel):
    """Trapezoidal trace cross-section. Both forms are allowed; physics normalizes them."""

    kind: Literal["angle", "top_width_delta"]
    value: float  # angle: etch angle in radians from the base (pi/2 = vertical walls);
    # top_width_delta: (w_bottom - w_top) in metres (Polar convention W1 - W2)
    reference: Literal["bottom", "top"] = "bottom"  # which width the drawn width denotes


class ConductorLayer(OAModel):
    kind: Literal["conductor"] = "conductor"
    id: LayerId  # "L1".."Ln"
    index: int = Field(ge=1)  # 1 = top copper
    source_name: str | None = None  # "F.Cu", "In1.Cu", "Top Layer", ...
    user_name: str | None = None  # alias from the source
    role: Literal["signal", "plane", "mixed", "jumper", "unknown"] = "signal"
    thickness: Length  # finished copper thickness (incl. plating if known)
    z_bottom: Coord
    z_top: Coord
    material: str = "copper"  # key into Stackup.materials (ConductorMaterial)
    roughness_top: Roughness = NoRoughness()  # surface facing +z
    roughness_bottom: Roughness = NoRoughness()  # surface facing -z
    roughness_side: Roughness = NoRoughness()
    etch: Etch | None = None  # None = rectangular cross-section
    plating_thickness: Length | None = None  # extra plating on outer layers, if separately known


class DielectricLayer(OAModel):
    kind: Literal["dielectric"] = "dielectric"
    id: LayerId  # "D1".."Dm"
    index: int = Field(ge=1)
    source_name: str | None = None  # "dielectric 1", sub-ply names are suffixed "/2", "/3"
    construction: Literal["core", "prepreg", "unknown"] = "unknown"
    thickness: Length = Field(gt=0.0)
    z_bottom: Coord
    z_top: Coord
    material: str  # key into Stackup.materials (DielectricMaterial)
    fills_between_traces: bool = True  # prepreg flows into the copper gaps of the adjacent layer(s)


class MaskLayer(OAModel):
    kind: Literal["mask"] = "mask"
    id: Literal["MT", "MB"]
    source_name: str | None = None
    thickness: Length  # thickness over bare laminate
    thickness_over_copper: Length | None = None  # C1/C2/C3 in Polar terms; None = same as thickness
    z_bottom: Coord
    z_top: Coord
    material: str
    present: bool = True


StackLayer = Annotated[ConductorLayer | DielectricLayer | MaskLayer, Field(discriminator="kind")]


class Stackup(OAModel):
    layers: tuple[StackLayer, ...]  # PHYSICAL ORDER, top (highest z) first
    materials: tuple[Material, ...]  # unique names
    finish: str | None = None  # "ENIG", "HASL", ...
    total_thickness: Length  # = layers[0].z_top
    known: bool = True  # False when the source had no stackup and defaults were used
    # validators:
    #  - z is contiguous: layers[i].z_bottom == layers[i+1].z_top (within GEOM_EPS); last z_bottom == 0
    #  - conductor ids L1..Ln are consecutive in order; dielectric ids D1..Dm consecutive
    #  - every material reference resolves, with the right kind
    #  - z_top - z_bottom == thickness
```

The rules for the ingest-to-model mapping:
- KiCad `addsublayer` plies become separate `DielectricLayer`s.
- KiCad gives a constant `epsilon_r`/`loss_tangent`. The reader emits `ConstantDkDf(f_ref=1 GHz)` with `source="ingest"`. The physics sidecar (section 11) overrides it.
- A board without a stackup gets KiCad's defaults, `known=False`, and the `INGEST.STACKUP_DEFAULTED` warning.

---

## 7. Nets, net classes and differential pairs

```python
# oa_model/nets.py
class Net(OAModel):
    id: NetId  # net name; KiCad "" (no net) is never a Net
    kind: Literal["signal", "power", "ground", "unknown"] = (
        "unknown"  # reader heuristic; setup intent overrides it
    )
    net_class: str | None = None  # NetClass.name
    source: SourceRef | None = None


class NetClass(OAModel):
    name: str
    track_width: Length | None = None
    clearance: Length | None = None
    via_diameter: Length | None = None
    via_drill: Length | None = None
    dp_width: Length | None = None
    dp_gap: Length | None = None
    target_z_se: float | None = Field(
        default=None, gt=0
    )  # ohm; from the source or parsed from name ("50ohm")
    target_z_diff: float | None = Field(default=None, gt=0)  # ohm; ("90ohm", "100R")
    target_tolerance: float | None = Field(default=None, gt=0, lt=1)  # fraction
    tuning_profile: str | None = None  # KiCad 10 tuning profile name (metadata)
    patterns: tuple[str, ...] = ()  # glob patterns that assign nets (informational)
    source: SourceRef | None = None


class DiffPair(OAModel):
    id: ItemId  # e.g. "dp:USB_D"
    p_net: NetId
    n_net: NetId
    origin: Literal["naming", "netclass", "source_explicit", "user"]
    target_z_diff: float | None = Field(default=None, gt=0)
```

The pairing heuristic (reader-side, deterministic): suffix pairs `_P/_N`, `P/N`, `+/-`, `_DP/_DN`, `_T/_C`, case-insensitive, with the same base name. Altium explicit pairs use `origin="source_explicit"`.

---

## 8. Padstacks, pads and vias

```python
# oa_model/padstack.py
class CircleShape(OAModel):
    kind: Literal["circle"] = "circle"
    diameter: Length


class RectShape(OAModel):
    kind: Literal["rect"] = "rect"
    width: Length
    height: Length


class RoundRectShape(OAModel):
    kind: Literal["roundrect"] = "roundrect"
    width: Length
    height: Length
    corner_radius: Length


class ObroundShape(OAModel):  # KiCad "oval"
    kind: Literal["obround"] = "obround"
    width: Length
    height: Length


class ChamferedRectShape(OAModel):
    kind: Literal["chamfered_rect"] = "chamfered_rect"
    width: Length
    height: Length
    chamfer: Length
    corners: tuple[Literal["tl", "tr", "bl", "br"], ...]
    corner_radius: Length = 0.0


class TrapezoidShape(OAModel):
    kind: Literal["trapezoid"] = "trapezoid"
    width: Length
    height: Length
    delta_x: Coord = 0.0
    delta_y: Coord = 0.0  # KiCad rect_delta semantics


class PolygonShape(OAModel):  # custom pads: anchor ∪ primitives, already unioned by the reader
    kind: Literal["polygon"] = "polygon"
    polygons: tuple[Polygon, ...]


PadShape = Annotated[
    CircleShape
    | RectShape
    | RoundRectShape
    | ObroundShape
    | ChamferedRectShape
    | TrapezoidShape
    | PolygonShape,
    Field(discriminator="kind"),
]
# All shapes are in the PAD-LOCAL frame: centred on the pad origin, unrotated, +y up.


class Drill(OAModel):
    kind: Literal["plated", "npth"]
    shape: Literal["round", "slot"] = "round"
    diameter: Length  # round: diameter; slot: width
    slot_length: Length | None = None  # slot: overall length along local x
    plating_thickness: Length = (
        25e-6  # barrel plating (default IPC class 2 ~ 25 um; sidecar overrides)
    )
    offset: Vec2 = (0.0, 0.0)  # drill centre relative to pad origin (pad-local frame)


class PadstackLayer(OAModel):
    layer: LayerId  # conductor layer id
    pad: PadShape | None  # None = no copper on this layer (non-functional pad removed)
    antipad: PadShape | None = None  # clearance cut into plane copper, if the source states it
    thermal_spoke_width: Length | None = None
    thermal_gap: Length | None = None
    connection: Literal["solid", "thermal", "none", "unknown"] = "unknown"  # to same-net zones


class Padstack(OAModel):
    id: ItemId  # shared by identical pads/vias (reader de-duplicates)
    drill: Drill | None  # None for SMD pads
    layers: tuple[PadstackLayer, ...]  # FULLY RESOLVED: one entry per conductor layer the pad
    # or via touches; front/inner/back/custom modes are expanded
    remove_unused_layers: bool = False
    keep_end_layers: bool = True
    mask_opening_top: PadShape | None = None  # solder-mask opening, pad-local frame
    mask_opening_bottom: PadShape | None = None
```

```python
# oa_model/pads.py
class Pad(OAModel):
    id: ItemId
    component: str | None  # refdes, None for free pads
    number: str  # "1", "A12", "" for mechanical pads
    padstack: ItemId
    position: Vec2  # board frame
    rotation: Angle  # absolute (board frame), CCW
    net: NetId | None
    kind: Literal["smd", "through_hole", "npth", "connector_edge", "aperture"]
    pin_function: str | None = None
    pin_type: str | None = None  # "power_in", "bidirectional", ... (source string)
    die_length: Length | None = None  # package length for timing
    die_delay: float | None = Field(default=None, ge=0)  # s (KiCad 10)
    source: SourceRef | None = None


# oa_model/vias.py
class Backdrill(OAModel):
    side: Literal["top", "bottom"]  # side the drill enters from
    diameter: Length
    last_removed_layer: LayerId  # deepest conductor layer whose barrel copper is removed
    must_not_cut: LayerId | None = (
        None  # first layer that must keep its barrel (= next layer inward)
    )
    depth: Length | None = None  # explicit depth from the entry surface, if the source gives it


class ViaProtection(OAModel):
    """IPC-4761 style protection. Affects thermal and DC models (filled vias conduct more)."""

    tented_top: bool = False
    tented_bottom: bool = False
    plugged: bool = False
    filled: Literal["none", "conductive", "non_conductive", "unknown"] = "none"
    fill_material: str | None = None  # material key when conductive
    capped: bool = False


class Via(OAModel):
    id: ItemId
    padstack: ItemId
    position: Vec2
    net: NetId | None
    kind: Literal["through", "blind", "buried", "micro"]
    start_layer: LayerId  # upper conductor layer of the span (smaller index)
    end_layer: LayerId  # lower conductor layer of the span
    backdrills: tuple[Backdrill, ...] = ()
    protection: ViaProtection = ViaProtection()
    source: SourceRef | None = None
```

Via semantics:
- The **span** is the plated barrel from `start_layer` to `end_layer`, inclusive.
- **Stub length** is the plated barrel left after backdrilling, beyond the last *used* layer. `oa_geometry` computes it, not the model. It needs connectivity to know which layers are used.
- KiCad 10 `secondary_drill (layers "F.Cu" "InX.Cu")` maps to `Backdrill(side="top", last_removed_layer=<InX id>)`, and `tertiary_drill` maps to `side="bottom"`. WP-101 must confirm with pcbnew whether the named end layer is itself cut and record the finding in the reader's docstring. If it is not cut, map the previous layer instead.

---

## 9. Copper geometry: tracks, zones and shapes

```python
# oa_model/copper.py
class Track(OAModel):
    """One routed conductor with a constant width along a centreline path. Readers emit one Track
    per source item (one KiCad segment or arc). Chaining into net paths is done by oa_geometry."""

    id: ItemId
    net: NetId | None
    layer: LayerId  # conductor layer
    width: Length = Field(gt=0.0)
    path: tuple[PathSeg, ...] = Field(min_length=1)  # contiguous: seg[i].end == seg[i+1].start
    end_caps: Literal["round", "square", "flat"] = "round"  # KiCad tracks have round caps
    source: SourceRef | None = None


class ZoneFill(OAModel):
    layer: LayerId
    polygons: tuple[Polygon, ...]  # ACTUAL copper (thermals, clearances already cut)


class KeepoutRules(OAModel):
    tracks: bool = False
    vias: bool = False
    pads: bool = False
    copper_pour: bool = False
    footprints: bool = False


class Zone(OAModel):
    id: ItemId
    net: NetId | None  # None for keepouts and unconnected pours
    layers: tuple[LayerId, ...]  # conductor layers
    priority: int = 0
    outline: Polygon  # user-drawn boundary (not copper)
    fills: tuple[ZoneFill, ...] = ()
    fill_state: Literal["filled", "unfilled", "stale_suspected", "refilled_by_tool"] = "filled"
    hatch: bool = False  # hatched fill (fills still hold the real copper)
    is_teardrop: bool = False
    keepout: KeepoutRules | None = None  # not None => rule area, no copper
    min_thickness: Length | None = None
    source: SourceRef | None = None


class CopperShape(OAModel):
    """Graphic copper that is neither a track, pad nor zone: gr_poly/gr_rect/gr_circle/text on Cu
    layers (already converted to polygons)."""

    id: ItemId
    net: NetId | None
    layer: LayerId
    polygon: Polygon
    origin: Literal["graphic", "text", "other"] = "graphic"
    source: SourceRef | None = None
```

`fill_state` rules for readers:
- A zone with copper fill enabled and no `filled_polygon` gets `"unfilled"` and the `INGEST.ZONE_UNFILLED` warning.
- If the file is newer than its last fill (heuristic: track/via geometry intersects the zone outline but not the fill and is not cleared), the reader sets `"stale_suspected"`.
- `"refilled_by_tool"` is set when the KiCad bridge refilled the zones before reading.

PI analyses must refuse zones that are `unfilled`, unless the user overrides it.

---

## 10. Components, outline, board

```python
# oa_model/board.py
class Component(OAModel):
    refdes: str  # unique
    value: str | None = None
    footprint: str | None = None  # "Lib:Name"
    side: Side
    position: Vec2
    rotation: Angle
    pads: tuple[ItemId, ...]  # Pad ids
    courtyard: Polygon | None = None  # for thermal source areas and EMI proximity
    body_height: Length | None = None
    dnp: bool = False
    exclude_from_bom: bool = False
    fields: tuple[tuple[str, str], ...] = ()  # selected properties (MPN, Manufacturer, ...)
    model_ref: str | None = None  # 3D model path (metadata)
    source: SourceRef | None = None


class BoardOutline(OAModel):
    polygon: Polygon  # outer edge (exterior) + internal cutouts (holes)
    edges: tuple[PathSeg, ...] = ()  # exterior edge as analytic segments (CCW), optional
    extra_boards: tuple[Polygon, ...] = ()  # additional disjoint outlines (panels); usually empty


class BoardMeta(OAModel):
    name: str
    source_tool: Literal["kicad", "ipc2581", "altium", "odbpp", "synthetic"]
    source_version: str | None = None  # e.g. "20260206" or "IPC-2581C"
    source_file: str | None = None  # file name (not an absolute path)
    source_hash: str | None = None  # sha256 of the source file bytes
    reader: str  # "oa-ingest-kicad 0.3.1"
    arc_tolerance: Length = 1e-6
    created_utc: str  # ISO-8601; EXCLUDED from content hash


class Board(OAModel):
    schema_version: Annotated[str, StringConstraints(pattern=r"^1\.\d+$")] = "1.0"
    meta: BoardMeta
    outline: BoardOutline
    stackup: Stackup
    nets: tuple[Net, ...]
    net_classes: tuple[NetClass, ...] = ()
    diff_pairs: tuple[DiffPair, ...] = ()
    padstacks: tuple[Padstack, ...] = ()
    pads: tuple[Pad, ...] = ()
    vias: tuple[Via, ...] = ()
    tracks: tuple[Track, ...] = ()
    zones: tuple[Zone, ...] = ()
    shapes: tuple[CopperShape, ...] = ()
    components: tuple[Component, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()  # from ingest

    def content_hash(self) -> str: ...  # section 14.4
```

The board model deliberately leaves out:
- Silkscreen, fab and courtyard graphics, apart from `Component.courtyard`.
- Design rules (clearances), apart from the net class values.
- Schematic data.
- Analysis setup. Ports, sources and loads live in setup YAML (section 12). They reference board ids and are not part of `Board`.

---

## 11. Physics sidecar (`<project>.oa-physics.yaml`)

The board file has no frequency-dependent Dk/Df, roughness, conductivity, plating, etch or thermal data, so all of it lives in a versioned sidecar next to the board. `oa_model.physics.apply_physics(board, overlay) -> Board` returns a new `Board` with the overlay merged. It is pure, deterministic, and part of oa-model.

```python
# oa_model/physics.py
class LayerSelector(OAModel):
    """Selects stack layers by id ('L3'), source name ('In2.Cu'), kind or glob."""

    ids: tuple[str, ...] = ()
    source_names: tuple[str, ...] = ()
    kinds: tuple[Literal["conductor", "dielectric", "mask"], ...] = ()


class LayerOverride(OAModel):
    select: LayerSelector
    material: str | None = None  # replace the material reference
    thickness: Length | None = None  # override (z is recomputed for the whole stack)
    roughness_top: Roughness | None = None
    roughness_bottom: Roughness | None = None
    roughness_side: Roughness | None = None
    etch: Etch | None = None
    plating_thickness: Length | None = None
    construction: Literal["core", "prepreg"] | None = None


class MaterialAlias(OAModel):
    """Maps a source material name ('FR4', '2116 RC58%') to a library or sidecar material."""

    source_name: str
    material: str


class PhysicsOverlay(OAModel):
    schema: Literal["oa-physics/1"]
    materials: tuple[Material, ...] = ()  # new or replacing (by name)
    aliases: tuple[MaterialAlias, ...] = ()
    layers: tuple[LayerOverride, ...] = ()  # applied in order
    defaults: PhysicsDefaults = PhysicsDefaults()  # class defined below; declare it first in code
    via_plating_thickness: Length | None = None
    temperature: float = 298.15  # K, board temperature for rho(T) unless an analysis overrides it


class PhysicsDefaults(OAModel):
    conductor_material: str = "copper"
    roughness: Roughness = NoRoughness()
    dielectric_model_upgrade: Literal["none", "djordjevic_sarkar"] = "djordjevic_sarkar"
    # 'djordjevic_sarkar': every ConstantDkDf left after aliasing becomes DS(dk, df, f_ref)
```

Example `myboard.oa-physics.yaml`:

```yaml
schema: oa-physics/1
materials:
  - kind: dielectric
    name: Isola_370HR_1080
    model: {kind: djordjevic_sarkar, dk: 3.92, df: 0.021, f_ref: 1GHz}
    dk_z_over_dk_xy: 0.95
    thermal: {k_xy: 0.8, k_z: 0.4}
  - kind: conductor
    name: copper_ed
    conductivity: 5.8e7
aliases:
  - {source_name: "FR4", material: Isola_370HR_1080}
layers:
  - select: {kinds: [conductor]}
    roughness_top: {kind: cannonball_huray, rz: 2.5um}
    roughness_bottom: {kind: cannonball_huray, rz: 5.0um}
    etch: {kind: angle, value: 60deg}
  - select: {source_names: ["F.Cu", "B.Cu"]}
    thickness: 48um            # 1 oz foil + plating
via_plating_thickness: 25um
```

A shipped material library (`oa_model/data/materials.yaml`, generic FR-4, common laminates and mask, values with citations) is resolved after the sidecar materials. It is data, not code, so it is extended without schema changes.

---

## 12. Design intent and ports (setup-side schemas, defined in oa-model)

These classes live in `oa_model.setup`. The setup YAML files use them (see interfaces.md §6), never `Board`.

### 12.1 Design intent

```python
# oa_model/setup/intent.py
class NetSelector(OAModel):
    """Selects nets by name glob, regex, net class or diff pair. The union of all criteria
    minus `exclude`."""

    names: tuple[str, ...] = ()  # globs: "USB_*", "/DDR/DQ*"
    regex: tuple[str, ...] = ()
    net_classes: tuple[str, ...] = ()
    diff_pairs: tuple[ItemId, ...] = ()
    kinds: tuple[Literal["signal", "power", "ground"], ...] = ()
    exclude: tuple[str, ...] = ()  # globs


class SignalIntent(OAModel):
    select: NetSelector
    high_speed: bool = True
    rise_time: float | None = Field(default=None, gt=0)  # s (10-90 %)
    f_max: Frequency | None = None  # default = 0.35 / rise_time
    target_z_se: float | None = None
    target_z_diff: float | None = None
    z_tolerance: float = 0.10
    reference_nets: tuple[NetId, ...] = ()  # allowed reference nets; empty = any ground/power
    io: bool = False  # leaves the board (EMI E9/E13)


class PowerIntent(OAModel):
    net: NetId
    return_net: NetId = "GND"
    v_nominal: float
    tolerance: float = 0.05
    i_max: float | None = None


class DesignIntent(OAModel):
    schema: Literal["oa-intent/1"] = "oa-intent/1"
    signals: tuple[SignalIntent, ...] = ()  # later entries override earlier ones per net
    power: tuple[PowerIntent, ...] = ()
    ground_nets: tuple[
        NetId, ...
    ] = ()  # explicit; default heuristic = names matching GND*, VSS*, AGND, DGND, PGND
    diff_pairs_add: tuple[DiffPair, ...] = ()
    diff_pairs_remove: tuple[ItemId, ...] = ()
```

`oa_model.setup.resolve_intent(board, intent) -> ResolvedIntent` gives the per-net intent: a frozen model with `by_net: tuple[tuple[NetId, NetIntentResolved], ...]` plus `index()`. Defaults are inferred from net classes (a `target_z_*` parsed from names like `90ohm` or `100R_diff`) and net kinds.

### 12.2 Ports

Ports are solver-facing definitions that reference board items. `oa_geometry` resolves them to geometry (interfaces.md §5 shows the resolved EM forms).

```python
# oa_model/setup/ports.py
class PadTerminal(OAModel):
    kind: Literal["pad"] = "pad"
    pad: ItemId | None = None  # either a pad id ...
    component: str | None = None  # ... or refdes + pin number
    pin: str | None = None


class PinGroupTerminal(OAModel):
    """All pads of a component on the given net(s), shorted into one terminal."""

    kind: Literal["pin_group"] = "pin_group"
    component: str
    nets: tuple[NetId, ...]
    pins: tuple[str, ...] = ()  # empty = all pins on those nets


class PointTerminal(OAModel):
    kind: Literal["point"] = "point"
    position: Vec2
    layer: LayerId
    net: NetId | None = None  # optional assertion; resolution fails if copper there is another net


class NearestReferenceTerminal(OAModel):
    """The nearest reference-plane copper under or around the positive terminal (via ReferenceMap)."""

    kind: Literal["nearest_reference"] = "nearest_reference"
    nets: tuple[NetId, ...] = ()  # restrict; empty = any ground net
    search_radius: Length = 2e-3


Terminal = Annotated[
    PadTerminal | PinGroupTerminal | PointTerminal | NearestReferenceTerminal,
    Field(discriminator="kind"),
]


class LumpedPort(OAModel):
    kind: Literal["lumped"] = "lumped"
    name: str
    positive: Terminal
    negative: Terminal
    z0: float = 50.0


class TraceEndPort(OAModel):
    """Wave- or TL-port where a trace crosses the extraction-region boundary. The resolver
    places the port on the region face; de-embedding shifts the reference plane by `deembed`."""

    kind: Literal["trace_end"] = "trace_end"
    name: str
    nets: tuple[NetId, ...]  # 1 net (single-ended) or 2 nets (differential, P first)
    side: Literal["xmin", "xmax", "ymin", "ymax", "auto"] = "auto"
    deembed: Length = 0.0
    z0: float | None = None  # None = renormalize to the computed line impedance
    modes: Literal["single", "differential", "both"] = "single"


class CoaxPort(OAModel):
    kind: Literal["coax"] = "coax"
    name: str
    pad: PadTerminal  # connector centre pin pad (through-hole or edge launch)
    outer_radius: Length
    inner_radius: Length | None = None  # default = drill radius
    dielectric: str = "PTFE"
    z0: float = 50.0


class PlanePairPort(OAModel):
    """PI/EMI port between two planes at a location (via-to-plane, decap site)."""

    kind: Literal["plane_pair"] = "plane_pair"
    name: str
    position: Vec2
    upper_layer: LayerId
    lower_layer: LayerId
    radius: Length = 0.15e-3


Port = Annotated[LumpedPort | TraceEndPort | CoaxPort | PlanePairPort, Field(discriminator="kind")]
```

---

## 13. Validation and indexing

```python
# oa_model/validate.py
def validate_board(board: Board, *, strict: bool = False) -> tuple[Diagnostic, ...]:
    """Semantic checks beyond pydantic structure. With strict=True any 'error' raises
    BoardValidationError. Checks (each has a stable code):
      MODEL.DUP_ID            duplicate item ids across all collections
      MODEL.DANGLING_REF      net/layer/padstack/material/component reference does not resolve
      MODEL.LAYER_KIND        track/zone/shape/padstack layer is not a conductor layer
      MODEL.VIA_SPAN          via span outside stackup, or start below end, or kind inconsistent with span
      MODEL.BACKDRILL         backdrill layer not within the span, or side inconsistent
      MODEL.POLY_ORIENT       wrong ring orientation; MODEL.POLY_INVALID self-intersection (checked with
                              a pure-numpy test on small rings; geometry service re-checks with GEOS)
      MODEL.TRACK_PATH        path not contiguous
      MODEL.DIFFPAIR          p/n net missing or identical
      MODEL.ZONE_FILL_LAYER   fill on a layer not in zone.layers
      MODEL.STACKUP_Z         z inconsistency
      MODEL.OUTLINE           outline missing/degenerate (warning: solvers that need it refuse)
    Runs in O(n) apart from polygon checks; < 2 s for a 50 MB board."""


class BoardIndex:
    """Read-only lookup tables built once per Board (weak-keyed cache: BoardIndex.of(board)).
    net(id), layer(id), conductor_layers (ordered), padstack(id), pad(id), via(id),
    component(refdes), items_by_net(net) -> ItemRefs, items_by_layer(layer)."""

    @classmethod
    def of(cls, board: Board) -> "BoardIndex": ...
```

---

## 14. Serialization and schema versioning policy

### 14.1 Versions
- `schema_version` uses `MAJOR.MINOR`. v1 is `"1.0"`.
- A **minor** bump (1.0 to 1.1) is **additive only**: new optional fields with defaults, new union members, new enum literals in fields marked "extensible" (`role`, `origin`, `Diagnostic.code`). A minor bump needs an ADR plus a changelog entry in `schemas/CHANGELOG.md`.
- A **major** bump (2.0) is anything else: rename, removal, a semantic change, or a unit change. It needs an ADR and a migration function `oa_model.migrations.v1_to_v2(dict) -> dict`. Readers keep loading every previous major through the chain of migrations.

### 14.2 Reader behaviour
- `load_board(path)` reads `schema_version`:
  - A lower or equal minor within the same major loads strictly (`extra="forbid"`).
  - A **higher minor** within the same major loads after pruning keys unknown to the running schema. Pruning walks the bundled `board-model.v1.json` and emits a `MODEL.NEWER_MINOR` warning listing the dropped paths.
  - A **higher major** raises `UnsupportedSchemaVersion`.
  - Lower majors run the migrations.
- Writers always write the running version.
- The published JSON Schemas are committed at `schemas/board-model.v1.json`, `schemas/oa-physics.v1.json` and `schemas/oa-intent.v1.json`, and CI regenerates them. Any diff that is not additive fails the `schema-compat` check unless the version is bumped.

### 14.3 File formats
- **`.oab.json`**: a single UTF-8 JSON document, with arrays inline as flat lists. It is canonical and diffable, and is the format for fixtures and small boards.
- **`.oab`**: a ZIP (store or deflate) containing `board.json`, where every `Points`/`Floats` value may be replaced by `{"$array": "a/<n>.npy"}`, plus `a/*.npy` files (little-endian float64, C order). Writers use it automatically above 5 MB of array data. Both forms describe the same logical document. `load_board` accepts both.
- Physics sidecar, intent and setup files are YAML and validated against their JSON Schemas.

### 14.4 Content hash
`Board.content_hash()` returns `"sha256:" + hex`. It is computed over a canonical encoding:
- JSON with sorted keys.
- Floats written with `repr` (round-trip exact).
- Every `Points`/`Floats` replaced by `{"$sha256": <array digest>}`.
- `meta.created_utc` and `meta.reader` excluded.

It is the cache key root for every downstream job (interfaces.md §4.4). The physics overlay, intent and setup have their own hashes. A `Board` after `apply_physics` hashes differently from the raw board, by design.

### 14.5 Fixture contract
`packages/oa-testkit` (WP-000) ships builders that construct synthetic boards directly in the model: `microstrip_board()`, `stripline_board()`, `diff_pair_board()`, `cpwg_board()`, `split_plane_board()`, `via_transition_board()`, `power_plane_board()`. Their `.oab.json` dumps are committed under `tests/fixtures/model/`. Every ingest adapter's contract test asserts `validate_board(strict=True)` passes on its outputs, and that round-trip `load_board(dump_board(b)) == b` holds.
