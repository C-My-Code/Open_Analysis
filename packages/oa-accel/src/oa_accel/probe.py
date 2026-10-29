# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Lazy CuPy discovery. This is the only module in the repository that imports ``cupy``.

``import oa_accel`` never touches CuPy; the first call to :func:`probe` or :func:`load_cupy` does.
"""

from __future__ import annotations

import importlib
import warnings
from dataclasses import dataclass, field
from types import ModuleType

# AMD gfx targets we treat as supported for ``auto``. Best effort, untested on hardware [verify
# against AMD's ROCm support list before relying on it].
ROCM_GFX_ALLOWLIST = ("gfx90a", "gfx942", "gfx1030", "gfx1100", "gfx1101", "gfx1102", "gfx1200")
MIN_CUDA_CC = (7, 0)

_cupy: ModuleType | None = None
_cupy_tried = False
_probe: Probe | None = None


@dataclass(frozen=True)
class DeviceInfo:
    kind: str  # "cuda" | "rocm"
    index: int
    name: str
    arch: str  # "8.9" on NVIDIA, "gfx1100" on AMD
    total_bytes: int  # [bytes]
    free_bytes: int  # [bytes]

    @property
    def supported(self) -> bool:
        """Whether ``auto`` may pick this device (ADR-0009 section 3, condition 1)."""
        if self.kind == "cuda":
            try:
                major, minor = (int(p) for p in self.arch.split("."))
            except ValueError:
                return False
            return (major, minor) >= MIN_CUDA_CC
        return self.arch.split(":")[0] in ROCM_GFX_ALLOWLIST


@dataclass(frozen=True)
class Probe:
    cupy_version: str | None = None
    nvmath_version: str | None = None
    cudss: bool = False  # nvmath.sparse.advanced.DirectSolver importable
    driver_version: str | None = None
    runtime_version: str | None = None
    devices: tuple[DeviceInfo, ...] = ()
    error: str | None = None  # why CuPy is unavailable, if it is
    notes: list[str] = field(default_factory=list, compare=False)

    @property
    def cupy_available(self) -> bool:
        return self.cupy_version is not None and bool(self.devices)


def load_cupy() -> ModuleType | None:
    """Import CuPy once; ``None`` when it is not installed or fails to load."""
    global _cupy, _cupy_tried
    if not _cupy_tried:
        _cupy_tried = True
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # "CUDA path could not be detected"
                _cupy = importlib.import_module("cupy")
        except Exception:  # missing wheel, missing driver, ABI mismatch
            _cupy = None
    return _cupy


def _version_str(v: int) -> str:
    return f"{v // 1000}.{(v % 1000) // 10}"


def _nvmath() -> tuple[str | None, bool]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            nv = importlib.import_module("nvmath")
            adv = importlib.import_module("nvmath.sparse.advanced")
        return str(getattr(nv, "__version__", "?")), hasattr(adv, "direct_solver")
    except Exception:
        return None, False


def probe(refresh: bool = False) -> Probe:
    """Describe the GPU stack. Cached; ``refresh=True`` re-queries."""
    global _probe
    if _probe is not None and not refresh:
        return _probe
    cp = load_cupy()
    if cp is None:
        _probe = Probe(error="cupy not installed or failed to import")
        return _probe
    try:
        rt = cp.cuda.runtime
        hip = bool(getattr(rt, "is_hip", False))
        kind = "rocm" if hip else "cuda"
        n = int(rt.getDeviceCount())
        devices = []
        for i in range(n):
            props = rt.getDeviceProperties(i)
            raw = props["name"]
            name = raw.decode() if isinstance(raw, bytes) else str(raw)
            if hip:
                gcn = props.get("gcnArchName", b"")
                arch = gcn.decode() if isinstance(gcn, bytes) else str(gcn)
            else:
                arch = f"{props['major']}.{props['minor']}"
            with cp.cuda.Device(i):
                free, total = rt.memGetInfo()
            devices.append(DeviceInfo(kind, i, name, arch, int(total), int(free)))
        nv_ver, cudss = _nvmath()
        _probe = Probe(
            cupy_version=str(cp.__version__),
            nvmath_version=nv_ver,
            cudss=cudss and not hip,
            driver_version=_version_str(int(rt.driverGetVersion())),
            runtime_version=_version_str(int(rt.runtimeGetVersion())),
            devices=tuple(devices),
        )
    except Exception as exc:  # no driver, no device
        _probe = Probe(cupy_version=str(getattr(cp, "__version__", "?")), error=repr(exc))
    return _probe
