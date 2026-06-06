"""DuckLake: managed tables, snapshots, and time travel.

The lake is an optional capability (needs the `ducklake` DuckDB extension). When
it isn't loadable in the test environment these tests skip rather than fail, the
same way the backend degrades gracefully at runtime.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.manifest import WORKSPACE_ENV


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(WORKSPACE_ENV, str(tmp_path))
    monkeypatch.delenv("SMOLDUCK_LAKE", raising=False)
    with TestClient(create_app()) as c:
        yield c


def _require_lake(client) -> dict:
    status = client.get("/api/lake/status").json()
    if not status.get("enabled"):
        pytest.skip(f"ducklake extension unavailable: {status.get('reason')}")
    return status


def test_status_reports_enabled_and_snapshot(client):
    status = _require_lake(client)
    assert status["data_path"].endswith("lake_files")
    assert status["current_snapshot"] is not None  # catalog-creation snapshot exists


def test_materialize_creates_managed_table_and_snapshot(client):
    _require_lake(client)
    before = client.get("/api/lake/status").json()["current_snapshot"]
    out = client.post("/api/lake/materialize", json={"table": "m", "sql": "SELECT 42 AS x"})
    assert out.status_code == 200
    assert out.json()["snapshot"] > before  # a write advances the snapshot

    # The managed table is queryable as lake.m.
    q = client.post("/api/query", json={"sql": "SELECT x FROM lake.m"}).json()
    assert q["rows"] == [[42]]

    versions = [s["version"] for s in client.get("/api/lake/snapshots").json()["snapshots"]]
    assert versions == sorted(versions) and len(versions) >= 2


def test_time_travel_query_at_version(client):
    _require_lake(client)
    client.post("/api/query", json={"sql": "CREATE TABLE lake.t AS SELECT 1 AS x"})
    pinned = client.get("/api/lake/status").json()["current_snapshot"]
    client.post("/api/query", json={"sql": "INSERT INTO lake.t VALUES (2), (3)"})

    head = client.post("/api/query", json={"sql": "SELECT count(*) AS n FROM lake.t"}).json()
    assert head["rows"][0][0] == 3
    old = client.post(
        "/api/query", json={"sql": f"SELECT count(*) AS n FROM lake.t AT (VERSION => {pinned})"}
    ).json()
    assert old["rows"][0][0] == 1  # as-of the pinned version, before the insert


def test_lake_disabled_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv(WORKSPACE_ENV, str(tmp_path))
    monkeypatch.setenv("SMOLDUCK_LAKE", "0")
    with TestClient(create_app()) as c:
        status = c.get("/api/lake/status").json()
        assert status["enabled"] is False
        assert c.post("/api/lake/materialize", json={"table": "m", "sql": "SELECT 1"}).status_code == 400
        assert c.get("/api/lake/snapshots").status_code == 400
        # backend still serves everything else
        assert c.get("/api/health").json()["status"] == "ok"
