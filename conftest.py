# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Repository-wide test defaults.

Several test suites (BEM, FEM, DC IR, openEMS) start one thread per core in their
numerical libraries. When many suites run at once, that oversubscribes the machine.
Cap thread counts unless the caller has already set them. This must run before
numpy or scipy are imported, which is why it lives in the rootdir conftest.
"""

import os

_DEFAULT_THREADS = "2"
for _var in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "OA_NUM_THREADS",  # openEMS engine threads (oa_em_openems)
):
    os.environ.setdefault(_var, _DEFAULT_THREADS)
