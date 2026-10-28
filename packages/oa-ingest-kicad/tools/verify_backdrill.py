# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Confirm with pcbnew which layers a KiCad 10 backdrill removes (board-model.md section 8).

Run with KiCad's bundled Python: ``python.exe verify_backdrill.py``. Result recorded in the
package README (KiCad 10.0.5): ``PCB_VIA.IsBackdrilledOrPostMachined(layer)`` is true for every
layer from the entry layer up to AND INCLUDING the named end layer, so the end layer is cut.
"""

from __future__ import annotations

import pcbnew


def main() -> None:
    b = pcbnew.BOARD()
    b.SetCopperLayerCount(6)
    v = pcbnew.PCB_VIA(b)
    v.SetViaType(pcbnew.VIATYPE_THROUGH)
    v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    b.Add(v)

    def size(mm: float) -> pcbnew.VECTOR2I:
        return pcbnew.VECTOR2I(pcbnew.FromMM(mm), pcbnew.FromMM(mm))

    v.SetSecondaryDrillSize(size(0.5))  # written to the file as (backdrill ...)
    v.SetSecondaryDrillStartLayer(pcbnew.F_Cu)
    v.SetSecondaryDrillEndLayer(pcbnew.In2_Cu)
    for layer in b.GetEnabledLayers().CuStack():
        print(f"{b.GetLayerName(layer):8s} cut={v.IsBackdrilledOrPostMachined(layer)}")


if __name__ == "__main__":
    main()
