# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""S-parameter assembly from port voltages/currents, renormalization and Touchstone I/O."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from oa_em_openems.postprocess import (
    PortRaw,
    RunRaw,
    assemble_s,
    line_impedance_reference,
    parse_convergence,
    shift_reference,
)
from oa_em_openems.touchstone import format_touchstone, read_touchstone, write_touchstone


def _runs_from_s(s: np.ndarray, a: np.ndarray, z: np.ndarray, freqs: np.ndarray) -> list[RunRaw]:
    """Raw V/I of N excitations for a network ``s`` driven with incident waves ``a[f, port, run]``
    referenced to the port impedances ``z`` (the 'solver' reference)."""
    b = np.einsum("fij,fjk->fik", s, a)
    root = np.sqrt(z)[None, :, None]
    v = root * (a + b)
    i = (a - b) / root
    n = s.shape[1]
    runs = []
    for k in range(n):
        ports = {
            j + 1: PortRaw(
                nr=j + 1,
                u=v[:, j, k],
                i=i[:, j, k],
                z_line=np.full(freqs.shape, z[j], dtype=complex),
                beta=np.zeros(freqs.shape, dtype=complex),
                shift=0.0,
            )
            for j in range(n)
        }
        runs.append(RunRaw(freqs=freqs, excited=k + 1, ports=ports))
    return runs


def _random_reciprocal(nf: int, n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    m = 0.3 * (rng.normal(size=(nf, n, n)) + 1j * rng.normal(size=(nf, n, n)))
    return (m + np.transpose(m, (0, 2, 1))) / 2


@pytest.mark.parametrize("n", [2, 3, 4])
def test_assembly_recovers_s_when_other_ports_are_not_matched(n: int) -> None:
    nf = 7
    freqs = np.linspace(1e9, 7e9, nf)
    s = _random_reciprocal(nf, n, n)
    z = np.array([50.0, 60.0, 40.0, 75.0][:n])
    rng = np.random.default_rng(3)
    a = np.eye(n)[None] + 0.2 * (rng.normal(size=(nf, n, n)) + 1j * rng.normal(size=(nf, n, n)))
    runs = _runs_from_s(s, a, z, freqs)
    got, notes = assemble_s(runs, list(range(1, n + 1)), z)
    assert notes == []
    assert np.allclose(got, s, atol=1e-12)


def test_renormalization_to_other_reference_impedances() -> None:
    nf, n = 5, 2
    freqs = np.linspace(1e9, 5e9, nf)
    s_src = _random_reciprocal(nf, n, 1)
    z_src = np.array([50.0, 50.0])
    a = np.broadcast_to(np.eye(n, dtype=complex), (nf, n, n)).copy()
    runs = _runs_from_s(s_src, a, z_src, freqs)
    z_new = np.array([75.0, 30.0])
    got, _ = assemble_s(runs, [1, 2], z_new)
    # reference: S' = (I - G S)^-1 ... via impedance matrices
    for k in range(nf):
        zmat = (
            np.sqrt(np.diag(z_src))
            @ (np.eye(n) + s_src[k])
            @ np.linalg.inv(np.eye(n) - s_src[k])
            @ np.sqrt(np.diag(z_src))
        )
        r = np.diag(z_new)
        expect = np.linalg.inv(np.sqrt(r)) @ (zmat - r) @ np.linalg.inv(zmat + r) @ np.sqrt(r)
        assert np.allclose(got[k], expect, atol=1e-10)


def test_missing_excitation_run_is_an_error() -> None:
    nf, n = 4, 2
    freqs = np.linspace(1e9, 4e9, nf)
    s = _random_reciprocal(nf, n, 5)
    z = np.array([50.0, 50.0])
    a = np.broadcast_to(np.eye(n, dtype=complex), (nf, n, n)).copy()
    with pytest.raises(ValueError, match="undetermined"):
        assemble_s(_runs_from_s(s, a, z, freqs)[:1], [1, 2], z)


def test_reference_plane_shift_rotates_a_forward_wave() -> None:
    f = np.array([1e9, 2e9])
    beta = 2 * np.pi * f / 1.5e8 + 0j
    z = np.full(2, 50.0 + 0j)
    shift = 4e-3
    u0 = np.exp(-1j * beta * 0.01) * np.array([1.0, 1.0])
    i0 = u0 / z
    u, i = shift_reference(u0, i0, z, beta, shift)
    expect = np.exp(-1j * beta * (0.01 + shift))
    assert np.allclose(u, expect) and np.allclose(i, expect / z)
    assert shift_reference(u0, i0, z, beta, 0.0)[0] is u0


def test_line_impedance_reference_is_the_median() -> None:
    f = np.array([1e9, 2e9, 3e9])
    z = np.array([49.0, 50.0, 53.0]) + 0j
    run = RunRaw(
        freqs=f,
        excited=1,
        ports={1: PortRaw(1, f * 0j, f * 0j, z, f * 0j, 0.0)},
    )
    assert line_impedance_reference([run], 1) == 50.0


def test_parse_convergence() -> None:
    text = (
        "[@        4s] Timestep:          992 || Speed:   24.6 MC/s (4.342e-03 s/TS) "
        "|| Energy: ~6.85e-20 (- 0.00dB)\n"
        "[@       12s] Timestep:         5084 || Speed:   56.5 MC/s (1.888e-03 s/TS) "
        "|| Energy: ~3.87e-17 (- 8.38dB)\nunrelated line\n"
    )
    assert parse_convergence(text) == [(992, 0.0), (5084, -8.38)]


@pytest.mark.parametrize("n", [1, 2, 3])
def test_touchstone_roundtrip_v1(tmp_path: Path, n: int) -> None:
    nf = 6
    freqs = np.linspace(1e8, 6e9, nf)
    s = _random_reciprocal(nf, n, 9) if n > 1 else _random_reciprocal(nf, 1, 9)
    path = tmp_path / f"x.s{n}p"
    write_touchstone(path, freqs, s, [50.0] * n, [f"P{i}" for i in range(n)], comments=("test",))
    text = path.read_text()
    assert "# HZ S RI R 50" in text and "! Port[1] = P0" in text
    f2, s2, z2 = read_touchstone(path)
    assert np.array_equal(f2, freqs) or np.allclose(f2, freqs, rtol=1e-12)
    assert np.allclose(s2, s, rtol=0, atol=1e-11)
    assert z2 == [50.0] * n


def test_touchstone_v2_for_unequal_references(tmp_path: Path) -> None:
    freqs = np.array([1e9, 2e9])
    s = _random_reciprocal(2, 2, 2)
    text = format_touchstone(freqs, s, [50.0, 75.0], ["A", "B"])
    assert "[Version] 2.0" in text and "[Reference] 50 75" in text and text.endswith("[End]\n")
    path = tmp_path / "x.s2p"
    path.write_text(text)
    _, s2, z2 = read_touchstone(path)
    assert np.allclose(s2, s, atol=1e-11) and z2 == [50.0, 75.0]
