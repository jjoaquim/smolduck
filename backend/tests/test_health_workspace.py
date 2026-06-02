from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.manifest import SMOLDUCK_DIRNAME, WORKSPACE_ENV


def _client(workspace: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv(WORKSPACE_ENV, str(workspace))
    return TestClient(create_app())


def test_health(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_workspace_creates_and_returns_manifest(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        resp = client.get("/api/workspace")
    assert resp.status_code == 200
    body = resp.json()
    assert body["workspace"] == str(tmp_path.resolve())
    manifest = body["manifest"]
    assert manifest["version"]
    assert manifest["sources"] == []
    assert manifest["settings"]["preview_row_cap"] == 1000

    # manifest.json was written to disk
    manifest_file = tmp_path / SMOLDUCK_DIRNAME / "manifest.json"
    assert manifest_file.exists()
    on_disk = json.loads(manifest_file.read_text())
    assert on_disk["version"] == manifest["version"]


def test_manifest_persists_across_restart(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        first = client.get("/api/workspace").json()["manifest"]

    # A fresh app instance (simulating a relaunch) reads the same manifest.
    with _client(tmp_path, monkeypatch) as client:
        second = client.get("/api/workspace").json()["manifest"]

    assert first["created_at"] == second["created_at"]


def test_store_duckdb_created(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.get("/api/health")
    assert (tmp_path / SMOLDUCK_DIRNAME / "store.duckdb").exists()
