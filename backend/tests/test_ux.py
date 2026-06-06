"""WS4 — analyst UX polish: structured SQL errors, query history, example loader."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.manifest import WORKSPACE_ENV


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(WORKSPACE_ENV, str(tmp_path))
    with TestClient(create_app()) as c:
        yield c


# --------------------------------------------------------------- structured errors

def test_syntax_error_carries_position_header(client):
    resp = client.post("/api/query", json={"sql": "SELCT 1"})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    # A concise locus header is prepended, and DuckDB's caret block is preserved.
    assert "line 1, column" in detail.lower()
    assert "^" in detail


def test_catalog_error_keeps_duckdb_hint(client):
    resp = client.post("/api/query", json={"sql": "SELECT * FROM no_such_table"})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "Catalog Error" in detail  # DuckDB's own classification leads (no lossy prefix)


# ------------------------------------------------------------------- query history

def test_history_roundtrip_and_dedup(client):
    assert client.get("/api/history").json()["history"] == []
    client.post("/api/history", json={"sql": "SELECT 1", "row_count": 1, "elapsed_ms": 2.0})
    client.post("/api/history", json={"sql": "SELECT 1", "row_count": 1})  # consecutive dup collapses
    client.post("/api/history", json={"sql": "SELECT 2"})
    hist = client.get("/api/history").json()["history"]
    assert [h["sql"] for h in hist] == ["SELECT 2", "SELECT 1"]  # newest first, deduped
    assert hist[0]["id"] and hist[0]["executed_at"]


def test_history_persists_and_clears(client):
    client.post("/api/history", json={"sql": "SELECT 42"})
    assert client.get("/api/history?limit=10").json()["history"][0]["sql"] == "SELECT 42"
    client.delete("/api/history")
    assert client.get("/api/history").json()["history"] == []


def test_history_rejects_blank(client):
    assert client.post("/api/history", json={"sql": "   "}).status_code == 400


# ------------------------------------------------------------------ example loader

def test_load_example_registers_queryable_view(client):
    listed = client.get("/api/examples").json()["examples"]
    assert any(e["name"] == "sales" for e in listed)

    out = client.post("/api/examples/load", params={"name": "sales"})
    assert out.status_code == 200
    view = out.json()["source"]["view_name"]
    assert view == "sales"

    # The generated demo data is immediately queryable.
    q = client.post("/api/query", json={"sql": f"SELECT count(*) AS n FROM {view}"}).json()
    assert q["rows"][0][0] == 600
    cols = {c["name"] for c in client.post("/api/query", json={"sql": f"SELECT * FROM {view} LIMIT 1"}).json()["columns"]}
    assert {"order_date", "channel", "region", "segment", "amount"} <= cols


def test_load_example_is_deterministic(client, tmp_path):
    first = client.post("/api/examples/load", params={"name": "sales"}).json()
    total1 = client.post("/api/query", json={"sql": "SELECT round(sum(amount),2) FROM sales"}).json()["rows"][0][0]
    # Re-loading regenerates the same seeded data → identical aggregate.
    client.post("/api/examples/load", params={"name": "sales"})
    total2 = client.post("/api/query", json={"sql": "SELECT round(sum(amount),2) FROM sales"}).json()["rows"][0][0]
    assert total1 == total2


def test_load_unknown_example_404(client):
    assert client.post("/api/examples/load", params={"name": "nope"}).status_code == 404
