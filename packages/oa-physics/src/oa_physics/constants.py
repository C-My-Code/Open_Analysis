# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Physical constants (SI 2019 values; mu0 uses the classical 4e-7 pi)."""

import math

MU0 = 4e-7 * math.pi  # H/m
C0 = 299_792_458.0  # m/s
EPS0 = 1.0 / (MU0 * C0**2)  # F/m
ETA0 = MU0 * C0  # ohm
