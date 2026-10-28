# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("oa_model")

from oa_model.arrays import Floats, NDArray, Points
from oa_results import (
    ArtifactOnly,
    ArtifactRef,
    ColumnSpec,
    FieldData,
    FieldPiece,
    FieldResult,
    NetworkResult,
    PortMapEntry,
    Provenance,
    QualityCriteria,
    QualityReport,
    ResultBundle,
    ResultItem,
    Severity,
    TableResult,
    TriMesh2D,
    Violation,
    ViolationSet,
    Waiver,
)
from oa_results.io import (
    bundle_artifact_path,
    from_touchstone,
    read_bundle,
    read_table_rows,
    sarif_document,
    to_csv,
    to_sarif,
    to_touchstone,
    to_vtu,
    write_bundle,
    write_table_parquet,
)

DATA = Path(__file__).parent / "data"


def _quality(f: np.ndarray) -> QualityReport:
    return QualityReport(
        verdict="pass",
        pqm=100.0,
        rqm=100.0,
        cqm=100.0,
        max_singular_value=0.9,
        worst_passivity_freq=None,
        reciprocity_max_err=0.0,
        dc="present",
        f_min=float(f[0]),
        f_max=float(f[-1]),
        n_freqs=len(f),
        criteria=QualityCriteria(),
    )


def _net(n: int = 4, nf: int = 7, z0_kind: str = "port") -> NetworkResult:
    rng = np.random.default_rng(1234)
    f = np.linspace(1e6, 10e9, nf)
    s = 0.3 * (rng.normal(size=(nf, n, n)) + 1j * rng.normal(size=(nf, n, n)))
    if z0_kind == "port":
        z0 = 50.0 + 1j * np.arange(n) * 0.1 + np.arange(n)
    elif z0_kind == "freq":
        z0 = 50.0 + 1j * rng.normal(size=(nf, n))
    else:
        z0 = np.full(n, 50.0 + 0j)
    names = tuple(f"port {i + 1}" for i in range(n))
    return NetworkResult(
        freqs=Floats(f),
        s=NDArray(s, dtype="c16"),
        z0=NDArray(z0, dtype="c16"),
        port_names=names,
        port_map=tuple(PortMapEntry(port=nm) for nm in names),
        quality=_quality(f),
    )


@pytest.mark.parametrize("z0_kind", ["port", "freq", "uniform"])
def test_touchstone_v2_bit_exact(tmp_path: Path, z0_kind: str) -> None:
    net = _net(4, z0_kind=z0_kind)
    p = to_touchstone(net, tmp_path / "a.s4p", "2.0")
    back = from_touchstone(p)
    assert np.array_equal(back.s.array, net.s.array)
    assert np.array_equal(back.freqs.array, net.freqs.array)
    assert np.array_equal(back.z0.array, net.z0.array)
    assert back.port_names == net.port_names
    assert back.quality.verdict == "warn"  # unassessed


def test_touchstone_v11_within_1e12(tmp_path: Path) -> None:
    net = _net(4)
    p = to_touchstone(net, tmp_path / "a.s4p", "1.1")
    assert "[Version]" not in p.read_text()
    back = from_touchstone(p)
    assert np.max(np.abs(back.s.array - net.s.array)) <= 1e-12
    assert np.max(np.abs(back.z0.array - net.z0.array)) <= 1e-12
    assert back.port_names == net.port_names
    assert "! Port[1] = port 1" in p.read_text()


def test_touchstone_two_port_order_and_skrf_crosscheck(tmp_path: Path) -> None:
    skrf = pytest.importorskip("skrf")
    net = _net(2, z0_kind="uniform")
    for version in ("1.1", "2.0"):
        p = to_touchstone(net, tmp_path / f"b{version[0]}.s2p", version)
        back = from_touchstone(p)
        assert np.array_equal(back.s.array, net.s.array)
        if version == "1.1":  # skrf does not know the v2.0 [Port Names] keyword
            ref = skrf.Network(str(p))
            assert np.allclose(ref.s, net.s.array, atol=1e-12)


def test_touchstone_reads_ma_and_db(tmp_path: Path) -> None:
    p = tmp_path / "c.s1p"
    p.write_text("# GHZ S MA R 50\n1.0 0.5 90\n2.0 1 0\n", encoding="utf-8")
    net = from_touchstone(p)
    assert np.allclose(net.s.array[0, 0, 0], 0.5j)
    assert np.allclose(net.freqs.array, [1e9, 2e9])
    p2 = tmp_path / "d.s1p"
    p2.write_text("# HZ S DB R 75\n1.0 -20 0\n", encoding="utf-8")
    net2 = from_touchstone(p2)
    assert np.allclose(net2.s.array[0, 0, 0], 0.1)
    assert net2.z0.array[0] == 75


def _table_item(tmp_path: Path) -> tuple[ResultItem, ArtifactRef, Path]:
    cols = (
        ColumnSpec(name="net", dtype="str", role="net"),
        ColumnSpec(name="z0", dtype="f8", unit="ohm"),
        ColumnSpec(name="n", dtype="i8"),
        ColumnSpec(name="ok", dtype="bool"),
    )
    rows = [("A", 50.5, 1, True), ("B", 49.0, 2, False)]
    pq = tmp_path / "src.parquet"
    ref = write_table_parquet(cols, rows, pq, name="tbl")
    item = ResultItem(id="t", title="Table", kind="table", data=TableResult(columns=cols, rows=ref))
    return item, ref, pq


def _violation(sev: Severity = "error", waived: bool = False) -> Violation:
    return Violation(
        rule_id="RP-001",
        severity=sev,
        message="Trace crosses a split",
        nets=("CLK",),
        layers=("L1",),
        items=("trk-1",),
        location=(0.001, 0.002),
        measured=1.5,
        limit=1.0,
        unit="m",
        fix_hint="Reroute",
        fingerprint="abc123",
        waived=Waiver(reason="known") if waived else None,
    )


def _bundle(tmp_path: Path) -> tuple[ResultBundle, dict[str, Path]]:
    net = _net(2, 5, "uniform")
    t_item, t_ref, t_path = _table_item(tmp_path)
    mesh = TriMesh2D(
        nodes=Points([0, 0, 1, 0, 0, 1]), triangles=NDArray([[0, 1, 2]], dtype="i8"), z=0.5
    )
    field = FieldResult(
        pieces=(
            FieldPiece(
                mesh=mesh,
                fields=(
                    FieldData(name="V", unit="V", location="node", values=NDArray([0.0, 1.0, 2.0])),
                    FieldData(name="q", unit="W", location="cell", values=NDArray([3.0])),
                ),
            ),
        )
    )
    blob = b"hello"
    import hashlib

    art = ArtifactRef(
        sha256=hashlib.sha256(blob).hexdigest(), media_type="text/plain", name="log.txt", size=5
    )
    (tmp_path / "log.txt").write_bytes(blob)
    bundle = ResultBundle(
        id="01HZZZZZZZZZZZZZZZZZZZZZZZ",
        status="ok",
        summary="demo",
        items=(
            ResultItem(id="n", title="S", kind="network", data=net),
            ResultItem(id="f", title="F", kind="field", data=field),
            t_item,
            ResultItem(
                id="v",
                title="V",
                kind="violations",
                data=ViolationSet.from_violations(("RP-001",), (_violation(),)),
            ),
            ResultItem(id="a", title="A", kind="artifact", data=ArtifactOnly(artifact=art)),
        ),
        provenance=Provenance(
            analysis="demo",
            analysis_version="1",
            board_hash="sha256:x",
            physics_hash=None,
            setup_hash="sha256:y",
            config={"a": 1},
            started_utc="2026-01-01T00:00:00Z",
            finished_utc="2026-01-01T00:00:01Z",
        ),
    )
    files = {t_ref.sha256: t_path, art.sha256: tmp_path / "log.txt"}
    return bundle, files


def test_bundle_round_trip(tmp_path: Path) -> None:
    bundle, files = _bundle(tmp_path)
    out = tmp_path / "res"
    write_bundle(bundle, out, lambda ref: files[ref.sha256])
    assert (out / "result.json").is_file()
    assert any((out / "a").glob("*.npy"))
    back = read_bundle(out)
    assert back == bundle
    net_in = bundle.items[0].data
    net_out = back.items[0].data
    assert isinstance(net_in, NetworkResult) and isinstance(net_out, NetworkResult)
    assert np.array_equal(net_out.s.array, net_in.s.array)
    tbl = back.items[2].data
    assert isinstance(tbl, TableResult) and tbl.rows is not None
    rows = read_table_rows(tbl, lambda r: bundle_artifact_path(out, r))
    assert rows == [("A", 50.5, 1, True), ("B", 49.0, 2, False)]
    art = back.items[4].data
    assert isinstance(art, ArtifactOnly)
    assert bundle_artifact_path(out, art.artifact).read_bytes() == b"hello"
    doc = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert doc["schema"] == "oa-result/1"
    assert "$array" in json.dumps(doc)


def test_csv_inline_and_parquet(tmp_path: Path) -> None:
    item, _ref, pq = _table_item(tmp_path)
    assert isinstance(item.data, TableResult)
    out = to_csv(item.data, tmp_path / "t.csv", lambda r: pq)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "net,z0 [ohm],n,ok"
    assert lines[1] == "A,50.5,1,True"
    inline = TableResult(columns=item.data.columns, inline=(("A", 1.0, 1, True),))
    assert to_csv(inline, tmp_path / "u.csv").read_text().splitlines()[1] == "A,1.0,1,True"


def test_sarif_validates_against_official_schema(tmp_path: Path) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((DATA / "sarif-schema-2.1.0.json").read_text(encoding="utf-8"))
    vs = ViolationSet.from_violations(
        ("RP-001", "RP-002"),
        (_violation("error"), _violation("warning", waived=True), _violation("info")),
    )
    p = to_sarif(vs, "board.kicad_pcb", tmp_path / "v.sarif")
    doc = json.loads(p.read_text(encoding="utf-8"))
    jsonschema.Draft4Validator(schema).validate(doc)
    levels = [r["level"] for r in doc["runs"][0]["results"]]
    assert levels == ["error", "warning", "note"]
    r0 = doc["runs"][0]["results"][0]
    assert r0["properties"]["location_mm"] == [1.0, 2.0]
    assert r0["locations"][0]["logicalLocations"][0] == {"name": "CLK", "kind": "net"}
    assert doc["runs"][0]["results"][1]["suppressions"][0]["justification"] == "known"
    empty = sarif_document(ViolationSet.from_violations((), ()))
    jsonschema.Draft4Validator(schema).validate(empty)


def test_vtu_export(tmp_path: Path) -> None:
    meshio = pytest.importorskip("meshio")
    bundle, _ = _bundle(tmp_path)
    field = bundle.items[1].data
    assert isinstance(field, FieldResult)
    (out,) = to_vtu(field, tmp_path / "f.vtu")
    m = meshio.read(str(out))
    assert m.points.shape == (3, 3)
    assert m.points[0, 2] == 0.5
    assert np.allclose(m.point_data["V"], [0, 1, 2])
    assert np.allclose(m.cell_data["q"][0], [3.0])
