# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Structured meshing errors.

``MeshError`` is an ``oa_model.errors.OAError`` carrying the ``{code, message, path, hint}`` fields
of ai-integration.md section 6. ``oa_framework.ErrorInfo`` sits in a higher layer than ``oa_mesh``,
so the wire form is produced by ``to_dict()``; the caller wraps it as
``ErrorInfo(**err.to_dict())``.
"""

from __future__ import annotations

from oa_model.errors import OAError

__all__ = ["MeshError"]


class MeshError(OAError):
    """Raised for every meshing failure. ``code`` is stable and machine readable:

    ============================  ============================================================
    ``MESH_EMPTY_GEOMETRY``       nothing is left after cleaning
    ``MESH_INVALID_GEOMETRY``     the input cannot be repaired into a valid polygon
    ``MESH_CONTACT_OUTSIDE``      a contact ring is not covered by copper
    ``MESH_CONTACT_OVERLAP``      two contacts overlap or share an id
    ``MESH_SIZE_FIELD``           a size field is malformed or too dense
    ``MESH_SETUP``                inconsistent arguments
    ``MESH_SESSION``              gmsh already initialized / session re-entered
    ``MESH_GMSH_FAILED``          gmsh raised, or produced a mesh that fails the checks
    ============================  ============================================================
    """

    code = "MESH_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        path: str | None = None,
        hint: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code)
        self.path = path
        self.hint = hint
        self.details: dict[str, object] = dict(details or {})

    def to_dict(self) -> dict[str, str | None]:
        """``{code, message, path, hint}``, the fields of ``oa_framework.ErrorInfo``."""
        return {"code": self.code, "message": self.message, "path": self.path, "hint": self.hint}
