from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.manifest import WORKSPACE_ENV

SPEC = {"data": [{"type": "bar", "x": ["North", "South"], "y": [3, 2]}], "layout": {"title": "by region"}}
CONFIG = {"type": "bar", "x": "region", "y": "n"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(WORKSPACE_ENV, str(tmp_path))
    with TestClient(create_app()) as c:
        yield c


def test_pin_list_get(client):
    created = client.post(
        "/api/charts",
        json={"title": "Regions", "query": "SELECT region, count(*) n FROM customers GROUP BY 1",
              "config": CONFIG, "spec": SPEC},
    ).json()
    assert created["title"] == "Regions"
    assert created["spec"]["data"][0]["type"] == "bar"

    listed = client.get("/api/charts").json()["charts"]
    assert len(listed) == 1 and listed[0]["id"] == created["id"]

    got = client.get(f"/api/charts/{created['id']}").json()
    assert got["config"] == CONFIG
    assert got["query"].startswith("SELECT region")


def test_pinned_chart_survives_relaunch(tmp_path, monkeypatch):
    monkeypatch.setenv(WORKSPACE_ENV, str(tmp_path))
    with TestClient(create_app()) as c:
        cid = c.post("/api/charts", json={"title": "Persisted", "spec": SPEC, "config": CONFIG}).json()["id"]

    assert (Path(tmp_path) / ".smolduck" / "charts" / f"{cid}.json").exists()

    with TestClient(create_app()) as c:
        got = c.get(f"/api/charts/{cid}").json()
        assert got["title"] == "Persisted"
        assert got["spec"]["data"][0]["x"] == ["North", "South"]


def test_delete_and_404(client):
    cid = client.post("/api/charts", json={"spec": SPEC}).json()["id"]
    assert client.delete(f"/api/charts/{cid}").status_code == 200
    assert client.get(f"/api/charts/{cid}").status_code == 404
    assert client.delete(f"/api/charts/{cid}").status_code == 404
    assert client.get("/api/charts/bad..id").status_code == 400


def test_chart_cell_config_persists(tmp_path, monkeypatch):
    monkeypatch.setenv(WORKSPACE_ENV, str(tmp_path))
    cells = [{"kind": "chart", "source": "SELECT 1 a, 2 b", "config": {"type": "scatter", "x": "a", "y": "b"}}]
    with TestClient(create_app()) as c:
        nb = c.post("/api/notebooks", json={"cells": cells}).json()
        nb_id = nb["id"]
        assert nb["cells"][0]["config"]["type"] == "scatter"
    with TestClient(create_app()) as c:
        got = c.get(f"/api/notebooks/{nb_id}").json()
        assert got["cells"][0]["config"] == {"type": "scatter", "x": "a", "y": "b"}
