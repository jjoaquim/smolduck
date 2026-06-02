from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.manifest import WORKSPACE_ENV

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
FILES = ["customers.csv", "orders.parquet", "refunds.json"]

EXPECTED_COLUMNS = {
    "customers": {"customer_id", "name", "region", "segment", "signup_date"},
    "orders": {"order_id", "customer_id", "order_date", "amount", "status", "channel"},
    "refunds": {"refund_id", "order_id", "amount", "reason", "refund_date"},
}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    for name in FILES:
        shutil.copy(FIXTURES / name, tmp_path / name)
    monkeypatch.setenv(WORKSPACE_ENV, str(tmp_path))
    return tmp_path


def _describe(client, view: str) -> set[str]:
    con = client.app.state.smol.db
    return {row[0] for row in con.execute(f'DESCRIBE "{view}"').fetchall()}


def test_register_folder_creates_three_views(workspace):
    with TestClient(create_app()) as client:
        resp = client.post("/api/sources", json={"path": "."})
        assert resp.status_code == 200
        registered = resp.json()["registered"]
        assert len(registered) == 3
        assert {s["kind"] for s in registered} == {"csv", "parquet", "json"}

        listed = client.get("/api/sources").json()["sources"]
        assert len(listed) == 3

        for view, cols in EXPECTED_COLUMNS.items():
            assert _describe(client, view) == cols


def test_delete_drops_view_and_entry(workspace):
    with TestClient(create_app()) as client:
        client.post("/api/sources", json={"path": "."})

        resp = client.delete("/api/sources/orders")
        assert resp.status_code == 200

        listed = client.get("/api/sources").json()["sources"]
        assert {s["id"] for s in listed} == {"customers", "refunds"}

        con = client.app.state.smol.db
        with pytest.raises(Exception):
            con.execute('DESCRIBE "orders"').fetchall()

    assert client.delete("/api/sources/orders") is not None  # router still mounted


def test_views_reconstruct_on_relaunch(workspace):
    with TestClient(create_app()) as client:
        client.post("/api/sources", json={"path": "."})

    # Fresh app instance against the same workspace (relaunch).
    with TestClient(create_app()) as client:
        listed = client.get("/api/sources").json()["sources"]
        assert len(listed) == 3
        assert _describe(client, "orders") == EXPECTED_COLUMNS["orders"]


def test_register_single_file_with_custom_view_name(workspace):
    with TestClient(create_app()) as client:
        resp = client.post(
            "/api/sources", json={"path": "customers.csv", "view_name": "people"}
        )
        assert resp.status_code == 200
        assert resp.json()["registered"][0]["view_name"] == "people"
        assert _describe(client, "people") == EXPECTED_COLUMNS["customers"]


def test_unsupported_and_missing_paths(workspace):
    with TestClient(create_app()) as client:
        assert client.post("/api/sources", json={"path": "nope.csv"}).status_code == 404
        (workspace / "notes.md").write_text("hi")
        assert client.post("/api/sources", json={"path": "notes.md"}).status_code == 400
