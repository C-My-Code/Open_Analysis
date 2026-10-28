# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""GLB (binary glTF 2.0) export of the model surfaces for the 3D UI preview."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import numpy as np

from oa_mesh._session import gmsh_session
from oa_mesh.errors import MeshError
from oa_mesh.occ3d._build import MM, construct
from oa_mesh.occ3d.types import OccModel

__all__ = ["export_glb"]

_COLORS = {
    "conductor": [0.85, 0.55, 0.2, 1.0],
    "via": [0.9, 0.7, 0.3, 1.0],
    "port": [0.9, 0.1, 0.1, 0.6],
    "boundary": [0.5, 0.6, 0.8, 0.25],
}


def export_glb(model: OccModel, path: Path, *, divisions: float = 30.0) -> dict[str, int]:
    """Write conductor, via, port and boundary surfaces as one glTF node per physical group.

    A coarse surface mesh (size = bounding-box diagonal / ``divisions``) is used, so the call is
    cheap and does not need the volume mesh. Positions are metres with z up (the board frame; no
    y-up conversion). Returns ``{group name: triangle count}``.
    """
    from oa_mesh.occ3d.mesher import _configure_geometry

    path = Path(path)
    b = model.bounds
    diag = float(np.linalg.norm(np.array(b[3:]) - np.array(b[:3]))) * MM
    with gmsh_session() as gm:
        _configure_geometry(gm)
        try:
            built = construct(gm, model)
            gm.option.setNumber("Mesh.MeshSizeFromPoints", 0)
            gm.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
            gm.option.setNumber("Mesh.MeshSizeMax", diag / divisions)
            gm.model.mesh.generate(2)
        except MeshError:
            raise
        except Exception as exc:
            raise MeshError(f"gmsh failed: {exc}", code="MESH_GMSH_FAILED") from exc
        nt, nc, _ = gm.model.mesh.getNodes()
        order = np.argsort(nt)
        tags = np.asarray(nt, dtype=np.int64)[order]
        xyz = (np.asarray(nc, dtype=np.float64).reshape(-1, 3)[order] / MM).astype(np.float32)
        per_group: dict[str, list[np.ndarray]] = {}
        kinds = {g.name: g.kind for g in built.groups}
        for s, name in sorted(built.surface_group.items()):
            types, _t, nn = gm.model.mesh.getElements(2, s)
            for ty, nodes in zip(types, nn, strict=True):
                if ty == 2:
                    per_group.setdefault(name, []).append(
                        np.searchsorted(tags, np.asarray(nodes, dtype=np.int64)).reshape(-1, 3)
                    )
    counts: dict[str, int] = {}
    blob = bytearray()
    views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []
    meshes: list[dict[str, Any]] = []
    nodes_json: list[dict[str, Any]] = []
    materials: list[dict[str, Any]] = []
    for name in sorted(per_group):
        tri = np.concatenate(per_group[name])
        used, inv = np.unique(tri, return_inverse=True)
        pos = np.ascontiguousarray(xyz[used], dtype="<f4")
        ind = np.ascontiguousarray(inv.reshape(-1, 3), dtype="<u4")
        for arr, target in ((pos, 34962), (ind, 34963)):
            while len(blob) % 4:
                blob.append(0)
            views.append(
                {"buffer": 0, "byteOffset": len(blob), "byteLength": arr.nbytes, "target": target}
            )
            blob += arr.tobytes()
        accessors.append(
            {
                "bufferView": len(views) - 2,
                "componentType": 5126,
                "count": len(pos),
                "type": "VEC3",
                "min": pos.min(axis=0).tolist(),
                "max": pos.max(axis=0).tolist(),
            }
        )
        accessors.append(
            {
                "bufferView": len(views) - 1,
                "componentType": 5125,
                "count": int(ind.size),
                "type": "SCALAR",
            }
        )
        color = _COLORS.get(kinds[name], [0.7, 0.7, 0.7, 1.0])
        materials.append(
            {
                "name": name,
                "doubleSided": True,
                "alphaMode": "BLEND" if color[3] < 1 else "OPAQUE",
                "pbrMetallicRoughness": {"baseColorFactor": color, "metallicFactor": 0.0},
            }
        )
        meshes.append(
            {
                "name": name,
                "primitives": [
                    {
                        "attributes": {"POSITION": len(accessors) - 2},
                        "indices": len(accessors) - 1,
                        "material": len(materials) - 1,
                    }
                ],
            }
        )
        nodes_json.append({"name": name, "mesh": len(meshes) - 1})
        counts[name] = len(tri)
    gltf = {
        "asset": {"version": "2.0", "generator": "oa_mesh.occ3d"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes_json)))}],
        "nodes": nodes_json,
        "meshes": meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(blob)}],
    }
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    blob += b"\0" * (-len(blob) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    with path.open("wb") as fh:
        fh.write(struct.pack("<III", 0x46546C67, 2, total))
        fh.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
        fh.write(struct.pack("<II", len(blob), 0x004E4942) + bytes(blob))
    return counts
