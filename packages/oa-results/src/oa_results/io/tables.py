# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Table writers: Parquet (the ``TableResult.rows`` artifact) and CSV."""

from __future__ import annotations

import csv
import hashlib
from collections.abc import Callable, Sequence
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from pydantic import JsonValue

from oa_results.artifact import ArtifactRef
from oa_results.tables import ColumnSpec, TableResult

__all__ = ["PARQUET_MEDIA_TYPE", "read_table_rows", "to_csv", "write_table_parquet"]

PARQUET_MEDIA_TYPE = "application/vnd.apache.parquet"
_ARROW = {"f8": pa.float64(), "i8": pa.int64(), "str": pa.string(), "bool": pa.bool_()}


def write_table_parquet(
    columns: Sequence[ColumnSpec],
    rows: Sequence[Sequence[JsonValue]],
    path: str | Path,
    *,
    name: str = "table",
) -> ArtifactRef:
    """Write ``rows`` as Parquet (uncompressed) and return the ``ArtifactRef`` of the file."""
    schema = pa.schema([(c.name, _ARROW[c.dtype]) for c in columns])
    cols = [pa.array([r[i] for r in rows], type=schema.types[i]) for i in range(len(columns))]
    table = pa.Table.from_arrays(cols, schema=schema)
    p = Path(path)
    pq.write_table(table, p, compression="none", write_statistics=False)
    data = p.read_bytes()
    return ArtifactRef(
        sha256=hashlib.sha256(data).hexdigest(),
        media_type=PARQUET_MEDIA_TYPE,
        name=name,
        size=len(data),
    )


def read_table_rows(
    table: TableResult, artifact_path: Callable[[ArtifactRef], Path] | None = None
) -> list[tuple[JsonValue, ...]]:
    """Rows of ``table``: the inline rows, else the Parquet artifact via ``artifact_path``."""
    if table.inline is not None:
        return [tuple(r) for r in table.inline]
    if table.rows is None or artifact_path is None:
        raise ValueError("table rows are in an artifact; pass artifact_path to resolve it")
    t = pq.read_table(artifact_path(table.rows))
    cols = [t.column(i).to_pylist() for i in range(t.num_columns)]
    return [tuple(c[i] for c in cols) for i in range(t.num_rows)]


def to_csv(
    table: TableResult,
    path: str | Path,
    artifact_path: Callable[[ArtifactRef], Path] | None = None,
) -> Path:
    """Write ``table`` as CSV; the header is ``name`` or ``name [unit]``."""
    p = Path(path)
    with open(p, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow([f"{c.name} [{c.unit}]" if c.unit else c.name for c in table.columns])
        for row in read_table_rows(table, artifact_path):
            w.writerow(["" if v is None else v for v in row])
    return p
