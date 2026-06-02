from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.manifest import WORKSPACE_ENV
from app.profile import profile_view

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
FILES = ["customers.csv", "orders.parquet", "refunds.json"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    for f in FILES:
        shutil.copy(FIXTURES / f, tmp_path / f)
    monkeypatch.setenv(WORKSPACE_ENV, str(tmp_path))
    with TestClient(create_app()) as c:
        c.post("/api/sources", json={"path": "."})
        yield c


def test_profile_customers(client):
    p = client.get("/api/sources/customers/profile")
    assert p.status_code == 200
    body = p.json()
    assert body["row_count"] == 12
    by = {c["name"]: c for c in body["columns"]}

    # numeric column → histogram whose bins cover every non-null row
    cid = by["customer_id"]
    assert cid["kind"] == "numeric"
    assert cid["distinct"] == 12  # exact, not the approximate SUMMARIZE value
    assert cid["null_pct"] == 0.0
    assert cid["histogram"] is not None
    assert sum(b["count"] for b in cid["histogram"]["bins"]) == 12

    # categorical → top-k
    region = by["region"]
    assert region["kind"] == "text"
    assert region["distinct"] == 4
    assert region["top_k"] is not None
    assert {t["value"] for t in region["top_k"]} == {"North", "South", "East", "West"}
    assert all(t["count"] == 3 for t in region["top_k"])


def test_profile_correlation_for_orders(client):
    body = client.get("/api/sources/orders/profile").json()
    corr = body["correlation"]
    assert corr is not None
    n = len(corr["columns"])
    assert n >= 2
    for i in range(n):
        assert corr["matrix"][i][i] == 1.0  # diagonal
        for j in range(n):
            assert corr["matrix"][i][j] == corr["matrix"][j][i]  # symmetric


def test_null_pct_and_distinct_match_manual_sql(client):
    """Spot-check the profile against hand-written SQL (exit criterion)."""
    state = client.app.state.smol
    state.db.execute(
        "CREATE VIEW nulltest AS SELECT * FROM (VALUES (1,'a'),(2,'a'),(NULL,'b'),(3,NULL)) t(x,y)"
    )
    by = {c["name"]: c for c in profile_view(state, "nulltest")["columns"]}

    manual_null_x = state.db.execute(
        "SELECT count(*) FILTER (WHERE x IS NULL) * 100.0 / count(*) FROM nulltest"
    ).fetchone()[0]
    manual_distinct_x = state.db.execute("SELECT count(DISTINCT x) FROM nulltest").fetchone()[0]

    assert by["x"]["null_pct"] == round(float(manual_null_x), 2) == 25.0
    assert by["x"]["distinct"] == manual_distinct_x == 3
    assert by["y"]["null_pct"] == 25.0
    assert by["y"]["distinct"] == 2


def test_profile_unknown_source_404(client):
    assert client.get("/api/sources/nope/profile").status_code == 404
