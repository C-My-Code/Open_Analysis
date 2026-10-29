# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""AccelReport: what actually ran (goes into result provenance and the job cache key)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AccelReport:
    backend: str = "cpu"  # "cpu" | "cuda" | "rocm"
    device_name: str = "cpu"
    device_id: int | None = None
    compute_capability: str | None = None
    driver_version: str | None = None
    runtime_version: str | None = None
    library_versions: dict[str, str] = field(default_factory=dict)
    peak_bytes: int = 0  # GPU memory pool high-water mark [bytes]
    budget_bytes: int = 0  # GPU memory budget [bytes]; 0 on CPU
    fallbacks: list[str] = field(default_factory=list)  # "<op>: <reason>", in order
    deterministic: bool = True  # False once an op without a deterministic mode ran
    untested_on_hardware: bool = False  # True for rocm (no AMD GPU in CI or on the dev machine)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def cache_key(self) -> str:
        """Device class and library major version only (ADR-0009 section 2)."""
        lib = "cupy" if self.backend != "cpu" else "scipy"
        ver = self.library_versions.get(lib, "0").split(".")[0]
        return f"{self.backend}/{lib}{ver}"
