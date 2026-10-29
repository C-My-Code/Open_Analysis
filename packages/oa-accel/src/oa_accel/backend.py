# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Backend protocol and the CPU / CuPy implementations (ADR-0009 section 2).

All entry points take host arrays (NumPy / SciPy sparse) and return host arrays unless
``host=False``, in which case the result stays on the device. A GPU failure (driver error, OOM,
unsupported op) falls back to the CPU oracle when ``fallback="cpu"`` and raises
:class:`AccelError` (``ACCEL_*``) when ``fallback="error"``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

import numpy as np

from oa_accel import ops
from oa_accel.errors import AccelError, ErrorInfo
from oa_accel.memory import (
    MemoryBudget,
    dense_solve_bytes,
    sparse_solve_bytes,
    stencil_bytes,
)
from oa_accel.report import AccelReport

Fallback = Literal["cpu", "error"]
Assume = Literal["gen", "sym", "pos"]
T = TypeVar("T")

# Failures that are the caller's problem, not the device's: never retried on the CPU.
_INPUT_CODES = frozenset({"ACCEL_NOT_CONVERGED", "ACCEL_SINGULAR", "ACCEL_BAD_INPUT"})


@runtime_checkable
class Backend(Protocol):
    name: str  # "cpu" | "cuda" | "rocm"
    xp: Any  # numpy or cupy
    sparse: Any  # scipy.sparse or cupyx.scipy.sparse
    memory: MemoryBudget
    fallback: Fallback

    def dense_solve(self, a: Any, b: Any, assume: Assume = "gen", *, host: bool = True) -> Any: ...

    def sparse_solve(
        self,
        a: Any,
        b: Any,
        method: str = "auto",
        m: Any = None,
        tol: float = 1e-10,
        *,
        assume: Literal["gen", "spd"] = "gen",
        maxiter: int | None = None,
        host: bool = True,
    ) -> Any: ...

    def sparse_matvec(self, a: Any, x: Any, *, host: bool = True) -> Any: ...

    def raster_stencil(self, u: Any, weights: tuple[float, ...], *, host: bool = True) -> Any: ...

    def to_device(self, x: Any) -> Any: ...

    def to_host(self, x: Any) -> Any: ...

    def synchronize(self) -> None: ...

    def memory_info(self) -> dict[str, int]: ...

    def report(self) -> AccelReport: ...


def _itemsize(x: Any) -> int:
    return int(np.dtype(x.dtype).itemsize)


def _rhs_cols(b: Any) -> int:
    return 1 if b.ndim == 1 else int(b.shape[1])


class CpuBackend:
    """NumPy / SciPy. Always available, bitwise deterministic, and the test oracle."""

    name: str = "cpu"
    xp: Any
    sparse: Any
    memory: MemoryBudget
    fallback: Fallback

    def __init__(self, fallback: Fallback = "cpu", notes: list[str] | None = None) -> None:
        import scipy.sparse as sp  # type: ignore[import-untyped,unused-ignore]

        self.xp = np
        self.sparse = sp
        self.fallback = fallback
        self.memory = MemoryBudget(gpu_bytes=0, pinned_bytes=0)
        self._report = AccelReport(
            backend="cpu",
            library_versions=_cpu_versions(),
            fallbacks=list(notes or []),
        )

    def dense_solve(self, a: Any, b: Any, assume: Assume = "gen", *, host: bool = True) -> Any:
        return ops.cpu_dense_solve(a, b, assume)

    def sparse_solve(
        self,
        a: Any,
        b: Any,
        method: str = "auto",
        m: Any = None,
        tol: float = 1e-10,
        *,
        assume: Literal["gen", "spd"] = "gen",
        maxiter: int | None = None,
        host: bool = True,
    ) -> Any:
        meth = "direct" if method == "auto" else method
        return ops.cpu_sparse_solve(a, b, meth, m, tol, maxiter)

    def sparse_matvec(self, a: Any, x: Any, *, host: bool = True) -> Any:
        return a @ x

    def raster_stencil(self, u: Any, weights: tuple[float, ...], *, host: bool = True) -> Any:
        return ops.stencil_apply(np, np.asarray(u), weights)

    def to_device(self, x: Any) -> Any:
        return x

    def to_host(self, x: Any) -> Any:
        return x

    def synchronize(self) -> None:
        return None

    def memory_info(self) -> dict[str, int]:
        return {}

    def report(self) -> AccelReport:
        return self._report


def _cpu_versions() -> dict[str, str]:
    import scipy

    return {"numpy": np.__version__, "scipy": scipy.__version__}


class CupyBackend:
    """CuPy on NVIDIA (``cuda``) or AMD (``rocm``, untested on hardware)."""

    name: str
    xp: Any
    sparse: Any
    memory: MemoryBudget
    fallback: Fallback

    def __init__(
        self,
        kind: str,
        device_id: int,
        budget: MemoryBudget,
        fallback: Fallback = "cpu",
        notes: list[str] | None = None,
    ) -> None:
        from oa_accel.probe import load_cupy, probe

        cp = load_cupy()
        pr = probe()
        if cp is None or device_id >= len(pr.devices):
            raise AccelError(
                ErrorInfo(
                    code="ACCEL_NO_DEVICE",
                    message=f"No {kind} device {device_id} is available.",
                    path="device",
                    hint="Install oa-accel[cuda12] and a current driver, or use OA_DEVICE=cpu.",
                )
            )
        import cupyx.scipy.sparse as csp

        self.name = kind
        self.device_id = device_id
        self.fallback = fallback
        self.memory = budget
        self._cp = cp
        self._cpu = CpuBackend(fallback)
        self.xp = cp
        self.sparse = csp
        self._csp = csp
        self._pr = pr
        self._fault: Exception | None = None
        self._peak = 0
        info = pr.devices[device_id]
        self._report = AccelReport(
            backend=kind,
            device_name=info.name,
            device_id=device_id,
            compute_capability=info.arch,
            driver_version=pr.driver_version,
            runtime_version=pr.runtime_version,
            library_versions={
                **_cpu_versions(),
                "cupy": pr.cupy_version or "",
                **({"nvmath": pr.nvmath_version} if pr.nvmath_version else {}),
            },
            budget_bytes=budget.gpu_bytes,
            fallbacks=list(notes or []),
            untested_on_hardware=(kind == "rocm"),
        )
        cp.cuda.Device(device_id).use()
        budget.apply(cp)

    # ---- plumbing

    def inject_fault(self, exc: Exception | None) -> None:
        """Make the next GPU launches raise ``exc`` (tests of the fallback path); None clears."""
        self._fault = exc

    def to_device(self, x: Any) -> Any:
        if hasattr(x, "tocsr") and not hasattr(x, "get"):  # scipy sparse
            return self._csp.csr_matrix(x.tocsr())
        if hasattr(x, "tocsr"):  # already a cupyx sparse matrix
            return x
        return self._cp.asarray(x)

    def to_host(self, x: Any) -> Any:
        return x.get() if hasattr(x, "get") else x

    def synchronize(self) -> None:
        self._cp.cuda.Device(self.device_id).synchronize()

    def memory_info(self) -> dict[str, int]:
        free, total = self._cp.cuda.runtime.memGetInfo()
        pool = self._cp.get_default_memory_pool()
        return {
            "device_free": int(free),
            "device_total": int(total),
            "pool_used": int(pool.used_bytes()),
            "pool_total": int(pool.total_bytes()),
            "pool_limit": int(pool.get_limit()),
        }

    def report(self) -> AccelReport:
        self._report.peak_bytes = max(self._peak, self._report.peak_bytes)
        return self._report

    def _classify(self, exc: Exception) -> ErrorInfo:
        name = type(exc).__name__
        if "OutOfMemory" in name:
            return ErrorInfo(
                code="ACCEL_OUT_OF_MEMORY",
                message="The GPU ran out of memory during the operation.",
                hint=f"Budget {self.memory.gpu_bytes} B. Raise OA_GPU_MEM_GB or use device cpu.",
            )
        return ErrorInfo(
            code="ACCEL_DEVICE_ERROR",
            message=f"{name}: {exc}",
            hint="Update the driver, check nvidia-smi, or use OA_DEVICE=cpu.",
        )

    def _dispatch(
        self, op: str, estimate: int, gpu_fn: Callable[[], T], cpu_fn: Callable[[], T]
    ) -> T:
        info: ErrorInfo
        try:
            if self._fault is not None:
                raise self._fault
            self.memory.require(estimate, op)  # refuses before any allocation
            out = gpu_fn()
            self.synchronize()
            self._peak = max(self._peak, int(self._cp.get_default_memory_pool().total_bytes()))
            return out
        except AccelError as exc:
            if exc.info.code in _INPUT_CODES:
                raise
            info = exc.info
        except Exception as exc:
            info = self._classify(exc)
        if self.fallback == "cpu":
            self._report.fallbacks.append(f"{op}: {info.code}")
            return cpu_fn()
        raise AccelError(info)

    def _out(self, x: Any, host: bool) -> Any:
        return self.to_host(x) if host else x

    # ---- operations

    def dense_solve(self, a: Any, b: Any, assume: Assume = "gen", *, host: bool = True) -> Any:
        est = dense_solve_bytes(a.shape[0], _rhs_cols(b), _itemsize(a), assume)
        return self._dispatch(
            "dense_solve",
            est,
            lambda: self._out(
                ops.gpu_dense_solve(self._cp, self.to_device(a), self.to_device(b), assume), host
            ),
            lambda: ops.cpu_dense_solve(self.to_host(a), self.to_host(b), assume),
        )

    def _resolve_method(self, method: str, assume: str) -> str:
        if method != "auto":
            return method
        if self.name == "cuda" and self._pr.cudss:
            return "direct"
        return "cg" if assume == "spd" else "gmres"

    def sparse_solve(
        self,
        a: Any,
        b: Any,
        method: str = "auto",
        m: Any = None,
        tol: float = 1e-10,
        *,
        assume: Literal["gen", "spd"] = "gen",
        maxiter: int | None = None,
        host: bool = True,
    ) -> Any:
        meth = self._resolve_method(method, assume)
        prec = "jacobi" if (method == "auto" and meth == "cg" and m is None) else m
        est = sparse_solve_bytes(a.shape[0], a.nnz, _rhs_cols(b), meth, _itemsize(a))

        def gpu() -> Any:
            if meth == "direct" and not (self.name == "cuda" and self._pr.cudss):
                raise AccelError(
                    ErrorInfo(
                        code="ACCEL_UNSUPPORTED",
                        message=f"No GPU sparse direct solver on {self.name} (needs cuDSS).",
                        path="sparse_solve.direct",
                        hint="Install oa-accel[cuda12] or use method='cg' or 'gmres'.",
                    )
                )
            return self._out(
                ops.gpu_sparse_solve(
                    self._cp, self.to_device(a), self.to_device(b), meth, prec, tol, maxiter, assume
                ),
                host,
            )

        def cpu() -> Any:
            cmeth = "direct" if meth == "direct" or method == "auto" else meth
            return ops.cpu_sparse_solve(self.to_host(a), self.to_host(b), cmeth, prec, tol, maxiter)

        out = self._dispatch("sparse_solve", est, gpu, cpu)
        if meth == "direct" and self._pr.cudss:
            # cuDSS exposes no documented deterministic mode we rely on [verify per release].
            self._report.deterministic = False
        return out

    def sparse_matvec(self, a: Any, x: Any, *, host: bool = True) -> Any:
        est = a.nnz * (_itemsize(a) + 4) + 2 * a.shape[0] * _itemsize(a)
        return self._dispatch(
            "sparse_matvec",
            est,
            lambda: self._out(self.to_device(a) @ self.to_device(x), host),
            lambda: self.to_host(a) @ self.to_host(x),
        )

    def raster_stencil(self, u: Any, weights: tuple[float, ...], *, host: bool = True) -> Any:
        est = stencil_bytes(tuple(u.shape), _itemsize(u))
        return self._dispatch(
            "raster_stencil",
            est,
            lambda: self._out(ops.stencil_apply(self._cp, self.to_device(u), weights), host),
            lambda: ops.stencil_apply(np, np.asarray(self.to_host(u)), weights),
        )
