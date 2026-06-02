from __future__ import annotations

import io
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.manifest import WORKSPACE_ENV

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
FILES = ["customers.csv", "orders.parquet", "refunds.json"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name in FILES:
        shutil.copy(FIXTURES / name, tmp_path / name)
    monkeypatch.setenv(WORKSPACE_ENV, str(tmp_path))
    with TestClient(create_app()) as c:
        c.post("/api/sources", json={"path": "."})
        yield c


def test_query_returns_typed_rows_and_timing(client):
    resp = client.post(
        "/api/query",
        json={"sql": "SELECT region, count(*) AS n FROM customers GROUP BY region ORDER BY region"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [c["name"] for c in body["columns"]] == ["region", "n"]
    assert {c["type"] for c in body["columns"]} >= {"VARCHAR", "BIGINT"}
    assert body["row_count"] == 4  # North/South/East/West
    assert body["truncated"] is False
    assert isinstance(body["elapsed_ms"], (int, float))


def test_pagination_cap_and_offset(client):
    first = client.post("/api/query", json={"sql": "SELECT * FROM range(50) t(i)", "limit": 10}).json()
    assert first["row_count"] == 10
    assert first["truncated"] is True
    assert first["rows"][0][0] == 0

    page2 = client.post(
        "/api/query", json={"sql": "SELECT * FROM range(50) t(i)", "limit": 10, "offset": 10}
    ).json()
    assert page2["rows"][0][0] == 10
    assert page2["truncated"] is True


def test_non_result_statement(client):
    body = client.post(
        "/api/query", json={"sql": "CREATE TEMP TABLE tmp_t AS SELECT 1 AS a"}
    ).json()
    assert body["statement"] is True
    assert body["columns"] == []


def test_invalid_sql_is_400(client):
    resp = client.post("/api/query", json={"sql": "SELECT * FROM no_such_view"})
    assert resp.status_code == 400


def test_export_parquet_roundtrip(client):
    resp = client.post("/api/query/export", json={"sql": "SELECT * FROM orders"})
    assert resp.status_code == 200
    assert "parquet" in resp.headers["content-type"]
    table = pq.read_table(io.BytesIO(resp.content))
    assert table.num_rows == 30
    assert set(table.column_names) == {
        "order_id", "customer_id", "order_date", "amount", "status", "channel"
    }


def test_ws_streams_arrow_batches(client):
    n = 2500
    with client.websocket_connect("/ws/query") as ws:
        ws.send_json({"sql": f"SELECT * FROM range({n}) t(i)", "batch_size": 1000})
        schema_msg = ws.receive_json()
        assert schema_msg["type"] == "schema"
        assert schema_msg["columns"][0]["name"] == "i"

        total = 0
        batches = 0
        end = None
        while True:
            msg = ws.receive()
            if "bytes" in msg and msg["bytes"] is not None:
                tbl = pa.ipc.open_stream(msg["bytes"]).read_all()
                total += tbl.num_rows
                batches += 1
            elif "text" in msg and msg["text"] is not None:
                import json

                end = json.loads(msg["text"])
                break
        assert end["type"] == "end"
        assert end["row_count"] == n
        assert total == n
        assert batches == 3


def test_ws_error_for_bad_sql(client):
    with client.websocket_connect("/ws/query") as ws:
        ws.send_json({"sql": "SELECT * FROM nope"})
        msg = ws.receive_json()
        assert msg["type"] == "error"
