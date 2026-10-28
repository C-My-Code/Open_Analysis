# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""REST endpoints over httpx/TestClient (FakeService, plus LocalService for jobs and WS)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pytest
from fastapi.testclient import TestClient

from oa_framework.registry import Registry as PluginRegistry
from oa_geometry import BoardGeometry
from oa_jobs import ToolsConfig
from oa_jobs.dummy import DummyAnalysis
from oa_server import create_app
from oa_service.local import LocalService
from oa_service.testing import FakeService

TOKEN = "t0k"
H = {"Authorization": f"Bearer {TOKEN}"}
API = "/api/v1"


@pytest.fixture
def fake(tmp_path: Path) -> FakeService:
    return FakeService(workdir=tmp_path / "fake")


@pytest.fixture
def client(fake: FakeService) -> TestClient:
    return TestClient(create_app(fake, token=TOKEN), headers=H)


def open_fixture(client: TestClient, fake: FakeService, name: str) -> str:
    r = client.post(f"{API}/projects/open", json={"path": str(fake.fixture_path(name))})
    assert r.status_code == 200, r.text
    return str(r.json()["id"])


def read_ipc(data: bytes) -> pa.Table:
    return pa.ipc.open_stream(io.BytesIO(data)).read_all()


def test_auth_required(fake: FakeService) -> None:
    c = TestClient(create_app(fake, token=TOKEN))
    r = c.get(f"{API}/projects")
    assert r.status_code == 403 and r.json()["error"]["code"] == "PERMISSION_DENIED"
    assert c.get(f"{API}/projects", headers={"Authorization": "Bearer nope"}).status_code == 403


def test_health_projects_board(client: TestClient, fake: FakeService) -> None:
    assert "version" in client.get(f"{API}/health").json()
    pid = open_fixture(client, fake, "diff_pair")
    lst = client.get(f"{API}/projects").json()
    assert [p["id"] for p in lst] == [pid] and lst[0]["board_hash"].startswith("sha256:")
    b = client.get(f"{API}/projects/{pid}/board").json()
    assert b["layer_count"] == 2
    full = client.get(f"{API}/projects/{pid}/board/full").json()
    assert full["schema_version"].startswith("1.")
    st = client.get(f"{API}/projects/{pid}/board/stackup").json()
    assert st["layers"] and "total_thickness" in st
    ev = client.get(f"{API}/projects/{pid}/board/stackup", params={"frequency": "3GHz"}).json()
    assert ev["frequency_hz"] == 3e9
    assert client.post(f"{API}/projects/{pid}/reload").status_code == 200


def test_nets_listing_headers_and_detail(client: TestClient, fake: FakeService) -> None:
    pid = open_fixture(client, fake, "diff_pair")
    r = client.get(f"{API}/projects/{pid}/board/nets", params={"limit": 1})
    body = r.json()
    total = int(r.headers["X-Total-Count"])
    assert len(body) == 1 and total >= 2 and r.headers["X-Next-Offset"] == "1"
    assert {"id", "kind", "net_class", "n_items", "length", "layers"} <= set(body[0])
    names, off = [], 0
    while True:
        rr = client.get(f"{API}/projects/{pid}/board/nets", params={"offset": off, "limit": 1})
        names += [n["id"] for n in rr.json()]
        if "X-Next-Offset" not in rr.headers:
            break
        off = int(rr.headers["X-Next-Offset"])
    assert names == sorted(names) and len(names) == total
    d = client.get(f"{API}/projects/{pid}/board/nets/{names[0]}")
    assert d.status_code == 200 and d.json()["info"]["name"] == names[0]


def test_error_bodies(client: TestClient, fake: FakeService) -> None:
    pid = open_fixture(client, fake, "microstrip")
    r = client.get(f"{API}/projects/{pid}/board/nets/NOPE")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert r.json()["error"]["details"]["candidates"]
    r = client.get(f"{API}/projects/p_zzz/board")
    assert r.status_code == 404
    r = client.post(
        f"{API}/projects/{pid}/jobs", json={"analysis": "dummy.work", "config": {"n_taks": 2}}
    )
    assert r.status_code == 422
    e = r.json()["error"]
    assert e["code"] == "SETUP_INVALID" and e["path"] == "/config/n_taks" and e["hint"]
    r = client.get(f"{API}/projects/{pid}/board/nets", params={"limit": 0})
    assert r.status_code == 422 and r.json()["error"]["path"]


def test_arrow_geometry_matches_oa_geometry(client: TestClient, fake: FakeService) -> None:
    pid = open_fixture(client, fake, "power_plane")
    board = fake.project(pid).board
    geom = BoardGeometry.build(board)
    for layer in geom.layer_ids():
        r = client.get(f"{API}/projects/{pid}/board/layers/{layer}/geometry")
        assert r.status_code == 200
        t = read_ipc(r.content)
        area = 0.0
        for offs, xy in zip(t["ring_offsets"].to_pylist(), t["xy"].to_pylist(), strict=True):
            pts = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
            bounds = [*offs, len(pts)]
            for k in range(len(offs)):
                ring = pts[bounds[k] : bounds[k + 1]]
                x, y = ring[:, 0], ring[:, 1]
                a = 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
                area += abs(a) if k == 0 else -abs(a)
        want = sum(g.area for g in geom.copper_by_net(layer).values()) * 1e6
        # nets overlap nowhere on one layer, so per-net areas add up
        assert area == pytest.approx(want, rel=1e-6, abs=1e-9)
        names = json.loads(t.schema.metadata[b"net_names"])
        assert names == sorted(names)
    bad = client.get(f"{API}/projects/{pid}/board/layers/L99/geometry")
    assert bad.status_code == 404
    tr = read_ipc(client.get(f"{API}/projects/{pid}/board/layers/L1/tracks").content)
    assert {"x0", "y0", "x1", "y1", "width", "net_idx", "item_idx"} <= set(tr.column_names)
    dr = read_ipc(client.get(f"{API}/projects/{pid}/board/drills").content)
    assert dr.column_names == ["x", "y", "d", "kind", "net_idx"]


def test_items_and_structure3d(client: TestClient, fake: FakeService) -> None:
    pid = open_fixture(client, fake, "microstrip")
    tid = fake.project(pid).board.tracks[0].id
    r = client.get(f"{API}/projects/{pid}/board/items/{tid}")
    assert r.status_code == 200 and r.json()["kind"] == "track"
    assert client.get(f"{API}/projects/{pid}/board/items/zzz").status_code == 404
    g = client.get(f"{API}/projects/{pid}/board/structure3d")
    assert g.status_code == 424 and g.json()["error"]["code"] == "ANALYSIS_UNAVAILABLE"


def test_jobs_results_and_commands(client: TestClient, fake: FakeService) -> None:
    pid = open_fixture(client, fake, "diff_pair")
    assert {a["kind"] for a in client.get(f"{API}/analyses").json()} >= {"erc", "dummy.work"}
    j = client.post(
        f"{API}/projects/{pid}/jobs", json={"analysis": "erc", "config": {}, "wait_s": 1}
    )
    assert j.status_code == 200 and j.json()["state"] == "succeeded"
    jid, rid = j.json()["id"], j.json()["result_id"]
    assert client.get(f"{API}/jobs/{jid}").json()["result_id"] == rid
    assert [x["id"] for x in client.get(f"{API}/projects/{pid}/jobs").json()] == [jid]
    man = client.get(f"{API}/results/{rid}").json()
    assert man["id"] == rid and man["items"]
    s = client.get(f"{API}/results/{rid}/summary").json()
    assert s["status"] == "warning"
    v = client.get(f"{API}/results/{rid}/violations", params={"limit": 2}).json()
    assert v["total"] == 3 and v["next_offset"] == 2
    fp = v["items"][0]["fingerprint"]
    assert (
        client.get(f"{API}/results/{rid}/violations/{fp}").json()["violation"]["fingerprint"] == fp
    )
    assert client.get(f"{API}/results/{rid}/export/json").status_code == 200
    assert client.get(f"{API}/results/{rid}/export/touchstone").status_code == 404
    assert client.post(f"{API}/jobs/{jid}/cancel").status_code == 200
    cmds = client.get(f"{API}/commands").json()
    assert "erc.run" in [c["name"] for c in cmds]
    env = client.post(f"{API}/commands/board.summary", json={"args": {"project_id": pid}}).json()
    assert env["ok"] and env["schema"] == "oa.board_summary/1"
    sc = client.get(f"{API}/schemas/oa-envelope/1")
    assert sc.status_code == 200 and sc.json()["$id"] == "oa-envelope/1"


def test_setups_crud(client: TestClient, fake: FakeService) -> None:
    pid = open_fixture(client, fake, "microstrip")
    body = {"name": "s1", "analysis": "erc", "config": {}}
    assert client.put(f"{API}/projects/{pid}/setups/s1", json=body).status_code == 200
    assert client.get(f"{API}/projects/{pid}/setups").json()["setups"][0]["name"] == "s1"
    assert client.get(f"{API}/projects/{pid}/setups/s1").json()["analysis"] == "erc"
    assert client.post(f"{API}/projects/{pid}/setups/s1/validate").json() == []
    assert (
        client.put(f"{API}/projects/{pid}/setups/x", json={**body, "name": "y"}).status_code == 400
    )
    assert client.delete(f"{API}/projects/{pid}/setups/s1").status_code == 204
    assert client.get(f"{API}/projects/{pid}/setups/s1").status_code == 404


def test_array_and_table_endpoints(client: TestClient, fake: FakeService) -> None:
    pid = open_fixture(client, fake, "microstrip")
    j = client.post(
        f"{API}/projects/{pid}/jobs", json={"analysis": "dummy.work", "config": {}}
    ).json()
    rid = j["result_id"]
    assert client.get(f"{API}/results/{rid}/items/scalars/arrays/nope").status_code == 404
    assert client.get(f"{API}/results/{rid}/items/scalars/table").status_code == 404


def local_with_dummy(tmp_path: Path) -> LocalService:
    reg = PluginRegistry.load()
    reg.analyses.setdefault("dummy.work", DummyAnalysis)  # type: ignore[arg-type]
    return LocalService(reg, cache_dir=tmp_path / "cache", tools=ToolsConfig({}), workers=2)


def test_ws_delivers_ordered_events_for_50_task_job(tmp_path: Path) -> None:
    svc = local_with_dummy(tmp_path)
    try:
        board = (
            Path(__file__).resolve().parents[3]
            / "tests"
            / "fixtures"
            / "model"
            / "microstrip.oab.json"
        )
        c = TestClient(create_app(svc, token=TOKEN), headers=H)
        pid = c.post(f"{API}/projects/open", json={"path": str(board)}).json()["id"]
        r = c.post(
            f"{API}/projects/{pid}/jobs",
            json={
                "analysis": "dummy.work",
                "config": {"n_tasks": 50, "work": 10},
                "executor": "inline",
            },
        )
        assert r.status_code == 200, r.text
        jid = r.json()["id"]
        with c.websocket_connect(f"{API}/events?token={TOKEN}") as ws:
            ws.send_json({"subscribe": [jid]})
            seqs, types = [], []
            while True:
                msg = ws.receive_json()
                assert msg["job_id"] == jid
                seqs.append(msg["seq"])
                types.append(msg["type"])
                if msg["type"] == "result":
                    break
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        assert types.count("task_finished") == 50
        with (
            pytest.raises(Exception),  # noqa: B017, PT011
            c.websocket_connect(f"{API}/events?token=bad") as ws2,
        ):
            ws2.receive_json()
    finally:
        svc.close()


def test_watcher_reloads_after_debounce(tmp_path: Path) -> None:
    from oa_server.watch import ProjectWatcher

    svc = FakeService(workdir=tmp_path / "w")
    h = svc.open_fixture("microstrip")
    sess = svc.project(h.project_id)
    f = tmp_path / "watched.txt"
    f.write_text("a")
    sess.watch_paths = (f,)
    w = ProjectWatcher(svc, debounce=0.3)
    assert w.poll(now=0.0) == []
    f.write_text("bb")
    assert w.poll(now=1.0) == []  # change noticed, debounce starts
    assert w.poll(now=1.1) == []  # not yet stable long enough
    assert w.poll(now=1.5) == [h.project_id]
    assert w.poll(now=2.0) == []


def test_openapi_committed_in_sync() -> None:
    from oa_server.openapi import render

    path = Path(__file__).resolve().parents[3] / "web" / "src" / "api" / "openapi.json"
    assert path.is_file(), "run: uv run python -m oa_server.openapi"
    assert path.read_text(encoding="utf-8") == render()


def test_openapi_has_no_board_full_surprises() -> None:
    from oa_server.openapi import build_openapi

    paths: dict[str, Any] = build_openapi()["paths"]
    assert f"{API}/projects/{{pid}}/board/full" in paths
    assert f"{API}/commands" in paths and f"{API}/schemas/{{schema_id}}" in paths
