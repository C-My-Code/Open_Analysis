# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``oa_native.bem_influence`` (WP-515) against the numpy oracle of ``bem.kernels``."""

from __future__ import annotations

import numpy as np
import pytest

from oa_native import get

native = get()
pytestmark = pytest.mark.skipif(
    native is None or not hasattr(native, "bem_influence"), reason="oa_native not built"
)


def _panels(n: int, seed: int) -> tuple[np.ndarray, ...]:
    rng = np.random.default_rng(seed)
    a = rng.uniform(-1e-3, 1e-3, (n, 2))
    ang = rng.uniform(0, 2 * np.pi, n)
    ln = rng.uniform(1e-6, 2e-4, n)
    tan = np.stack([np.cos(ang), np.sin(ang)], axis=1)
    nrm = np.stack([-tan[:, 1], tan[:, 0]], axis=1)
    b = a + tan * ln[:, None]
    mid = 0.5 * (a + b)
    return mid, nrm, a, b, tan, nrm, ln


@pytest.mark.parametrize("threads", [1, 3])
def test_matches_numpy_oracle(threads: int) -> None:
    from oa_impedance.bem.kernels import influence

    mid, normal, a, b, tan, nrm, ln = _panels(211, 1)
    p_ref, d_ref = influence(mid, normal, a, b, tan, nrm, ln)
    d_ref[np.arange(211), np.arange(211)] = 0.0
    p = np.empty((211, 211))
    d = np.empty((211, 211))
    native.bem_influence(mid, normal, a, tan, nrm, ln, p, d, True, threads)
    # same f64 expressions; only ln / atan2 differ in the last ulp
    assert np.max(np.abs(p - p_ref)) <= 1e-13 * np.max(np.abs(p_ref))
    assert np.max(np.abs(d - d_ref)) <= 1e-13 * np.max(np.abs(d_ref))
    assert np.all(np.diag(d) == 0.0)


def test_rejects_bad_shapes() -> None:
    mid, normal, a, _b, tan, nrm, ln = _panels(8, 2)
    p = np.empty((8, 7))
    d = np.empty((8, 8))
    with pytest.raises(ValueError, match="outputs must be"):
        native.bem_influence(mid, normal, a, tan, nrm, ln, p, d, False, 1)
