# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared test helpers: the tests directory is put on ``sys.path`` so ``_xs`` can be imported."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(scope="session", autouse=True)
def _warm_gpu_kernels() -> None:
    """With ``OA_RUN_GPU=1``, build the CUDA kernels of the BEM before the memory hungry FEM tests.

    NVRTC compiles a kernel on first use and loads its runtime DLLs then. After the FEM tests the
    pytest process sits near the 8 GB job cap (the CuPy pool counts as committed memory on
    Windows) and that load fails; the BEM then falls back to the host and the GPU tests see no
    device. Compiled kernels are cached on disk and in the process."""
    if os.environ.get("OA_RUN_GPU") != "1":
        return
    saved = os.environ.get("OA_DEVICE")
    os.environ["OA_DEVICE"] = "cuda"
    try:
        import _xs

        from oa_impedance.bem import solve_bem

        solve_bem(_xs.stripline(0.5, 1.0, 0.1), [0.0, 1e9])
    except Exception:  # no usable device: the GPU tests skip or fail on their own terms
        pass
    finally:
        if saved is None:
            os.environ.pop("OA_DEVICE", None)
        else:
            os.environ["OA_DEVICE"] = saved
