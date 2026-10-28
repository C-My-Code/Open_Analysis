# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regenerate ``tests/golden/network_quality/*/line.s2p`` (run: ``python make_fixtures.py``).

The committed Touchstone files are the fixtures; the tests never call this script.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import skrf
from synth import line_network

ROOT = Path(__file__).resolve().parents[3] / "tests" / "golden" / "network_quality"
F_DC = np.arange(0.0, 20.0e9 + 1.0, 100e6)
F_10M = np.arange(10e6, 20.0e9 + 1.0, 100e6)

EXPECTED = {
    "passive_line": ("pass", "present"),
    "passivity_violation": ("fail", "present"),
    "non_reciprocal": ("fail", "present"),
    "non_causal_const_dk": ("warn", "present"),
    "starts_at_10mhz": ("pass", "extrapolated"),
}
SOURCES = {
    "passive_line": "analytic: 30 mm lossy line, Djordjevic-Sarkar Dk 4 Df 0.02, DC to 20 GHz",
    "passivity_violation": "analytic: passive_line with S21 and S12 scaled by 1.05",
    "non_reciprocal": "analytic: passive_line with S12 scaled by 0.8",
    "non_causal_const_dk": "analytic: same line with constant Dk 4 Df 0.2 (non-causal dielectric)",
    "starts_at_10mhz": "analytic: passive_line sampled from 10 MHz (no DC point)",
}


def build() -> dict[str, skrf.Network]:
    base = line_network(F_DC)
    viol = base.copy()
    viol.s[:, 1, 0] *= 1.05
    viol.s[:, 0, 1] *= 1.05
    nonrec = base.copy()
    nonrec.s[:, 0, 1] *= 0.8
    return {
        "passive_line": base,
        "passivity_violation": viol,
        "non_reciprocal": nonrec,
        "non_causal_const_dk": line_network(F_DC, causal=False, df=0.2),
        "starts_at_10mhz": line_network(F_10M),
    }


def main() -> None:
    for name, net in build().items():
        d = ROOT / name
        d.mkdir(parents=True, exist_ok=True)
        net.write_touchstone("line", str(d), form="ri", skrf_comment=False)
        verdict, dc = EXPECTED[name]
        (d / "case.toml").write_text(
            f'source = "{SOURCES[name]}"\n\n[tolerance]\nrtol = 1e-9\natol = 0.0\n\n'
            f'[inputs]\nfile = "line.s2p"\n\n[expected]\nverdict = "{verdict}"\ndc = "{dc}"\n',
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
