# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``pdn`` service stub (interfaces.md section 3.4): in-process DC solves for thermal coupling."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from oa_geometry import BoardGeometry
from oa_model.board import Board
from oa_pi.dc.config import DcConfig
from oa_pi.dc.fem import DcSolution, solve_fem
from oa_pi.dc.network import TemperatureField, build_network

__all__ = ["PdnService", "make_service"]


class PdnService:
    """Bound to a board with ``bind``; ``dc_solve(rail, T_field)`` re-solves with rho(T).

    ``T_field`` is a uniform temperature [K] or ``f(layer_id, xy (n, 2)) -> T (n,)`` [K]; the
    returned ``DcSolution`` carries ``|J|`` and resistivity per triangle, so the Joule density
    is ``rho * J^2`` [W/m^3].
    """

    def __init__(self, board: Board | None = None, geometry: BoardGeometry | None = None) -> None:
        self._board = board
        self._geo = geometry

    def bind(self, board: Board, geometry: BoardGeometry | None = None) -> PdnService:
        return PdnService(board, geometry)

    def dc_solve(
        self, rail: DcConfig | Mapping[str, Any], T_field: TemperatureField | None = None
    ) -> DcSolution:
        if self._board is None:
            raise RuntimeError("PdnService is not bound to a board; call bind(board) first")
        cfg = rail if isinstance(rail, DcConfig) else DcConfig.model_validate(rail)
        geo = self._geo or BoardGeometry.build(self._board)
        net = build_network(self._board, geo, cfg, T_field)
        nets = {cfg.rail.net, cfg.rail.return_}
        return solve_fem(net, tracks=[t for t in self._board.tracks if t.net in nets])


def make_service() -> PdnService:
    """Entry-point factory (group ``open_analysis.services``)."""
    return PdnService()
