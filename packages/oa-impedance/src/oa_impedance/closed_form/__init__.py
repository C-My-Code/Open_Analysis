# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Closed-form transmission-line models. Every function returns a ``CFResult`` with validity."""

from ._common import CFResult, RangeCheck, Validity, kratio
from .coupled import edge_coupled_microstrip
from .cpw import cpw, hilberg_kratio
from .legacy import ipc2141_microstrip, ipc2141_stripline
from .loss import dielectric_loss_np_per_m, wheeler_conductor_loss_np_per_m
from .microstrip import (
    embedded_microstrip,
    hj_eeff,
    hj_z01,
    kj_eeff,
    kj_z0,
    microstrip_hj,
    microstrip_wheeler,
)
from .stripline import (
    cohn_z0,
    edge_coupled_stripline,
    offset_stripline,
    stripline,
    wheeler_z0,
)

__all__ = [
    "CFResult",
    "RangeCheck",
    "Validity",
    "cohn_z0",
    "cpw",
    "dielectric_loss_np_per_m",
    "edge_coupled_microstrip",
    "edge_coupled_stripline",
    "embedded_microstrip",
    "hilberg_kratio",
    "hj_eeff",
    "hj_z01",
    "ipc2141_microstrip",
    "ipc2141_stripline",
    "kj_eeff",
    "kj_z0",
    "kratio",
    "microstrip_hj",
    "microstrip_wheeler",
    "offset_stripline",
    "stripline",
    "wheeler_conductor_loss_np_per_m",
    "wheeler_z0",
]
