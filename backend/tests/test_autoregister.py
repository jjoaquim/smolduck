from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.manifest import WORKSPACE_ENV

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
FILES = ["customers.csv", "orders.parquet", "refunds.json"]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    for f in FILES:
        shutil.copy(FIXTURES / f, tmp_path / f)
    monkeypatch.setenv(WORKSPACE_ENV, str(tmp_path))
    return tmp_path


def test_first_launch_autoregisters(workspace):
    with TestClient(create_app()) as c:
        sources = c.get("/api/sources").json()["sources"]
        assert {s["view_name"] for s in sources} == {"customers", "orders", "refunds"}
        # the views are queryable immediately
        assert c.post("/api/query", json={"sql": "SELECT count(*) FROM customers"}).json()["rows"][0][0] == 12


def test_second_launch_does_not_duplicate(workspace):
    with TestClient(create_app()):
        pass
    with TestClient(create_app()) as c:
        sources = c.get("/api/sources").json()["sources"]
        assert len(sources) == 3  # reconciled, not re-registered into duplicates
