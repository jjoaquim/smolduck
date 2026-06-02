from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.manifest import WORKSPACE_ENV

FOUR_CELLS = [
    {"kind": "markdown", "source": "# Analysis"},
    {"kind": "sql", "source": "SELECT 1 AS a", "last_result": {"rows": [[1]], "columns": [{"name": "a"}]}},
    {"kind": "python", "source": "print('hi')"},
    {"kind": "chart", "source": "{\"type\": \"bar\"}"},
]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv(WORKSPACE_ENV, str(tmp_path))
    return tmp_path


def test_create_list_get_roundtrip(workspace):
    with TestClient(create_app()) as client:
        created = client.post("/api/notebooks", json={"title": "My NB", "cells": FOUR_CELLS})
        assert created.status_code == 200
        nb = created.json()
        assert nb["title"] == "My NB"
        assert len(nb["cells"]) == 4
        assert [c["kind"] for c in nb["cells"]] == ["markdown", "sql", "python", "chart"]
        assert all(c["id"] for c in nb["cells"])  # ids minted

        listed = client.get("/api/notebooks").json()["notebooks"]
        assert len(listed) == 1
        assert listed[0]["cell_count"] == 4

        got = client.get(f"/api/notebooks/{nb['id']}").json()
        assert got == nb


def test_four_cells_and_order_restore_on_relaunch(workspace):
    with TestClient(create_app()) as client:
        nb = client.post("/api/notebooks", json={"title": "Persisted", "cells": FOUR_CELLS}).json()
        nb_id = nb["id"]
        order = [c["kind"] for c in nb["cells"]]

    # On-disk file is the source of truth.
    assert (Path(workspace) / ".smolduck" / "notebooks" / f"{nb_id}.json").exists()

    # Fresh app instance against the same workspace (relaunch).
    with TestClient(create_app()) as client:
        got = client.get(f"/api/notebooks/{nb_id}").json()
        assert got["title"] == "Persisted"
        assert [c["kind"] for c in got["cells"]] == order
        # cached last_result survives so reopen shows prior output
        sql_cell = next(c for c in got["cells"] if c["kind"] == "sql")
        assert sql_cell["last_result"]["rows"] == [[1]]


def test_update_reorders_and_edits(workspace):
    with TestClient(create_app()) as client:
        nb = client.post("/api/notebooks", json={"cells": FOUR_CELLS}).json()
        cells = nb["cells"]
        reversed_cells = list(reversed(cells))
        updated = client.put(
            f"/api/notebooks/{nb['id']}",
            json={"title": "Renamed", "cells": reversed_cells},
        )
        assert updated.status_code == 200
        body = updated.json()
        assert body["title"] == "Renamed"
        assert [c["kind"] for c in body["cells"]] == ["chart", "python", "sql", "markdown"]
        # ids preserved across reorder (not re-minted)
        assert {c["id"] for c in body["cells"]} == {c["id"] for c in cells}
        assert body["updated_at"] >= nb["updated_at"]


def test_delete_and_404s(workspace):
    with TestClient(create_app()) as client:
        nb = client.post("/api/notebooks", json={}).json()
        assert client.get(f"/api/notebooks/{nb['id']}").status_code == 200
        assert client.delete(f"/api/notebooks/{nb['id']}").status_code == 200
        assert client.get(f"/api/notebooks/{nb['id']}").status_code == 404
        assert client.put(f"/api/notebooks/{nb['id']}", json={"cells": []}).status_code == 404
        assert client.delete(f"/api/notebooks/{nb['id']}").status_code == 404
        assert client.get("/api/notebooks/has..dots").status_code == 400


def test_empty_create_defaults(workspace):
    with TestClient(create_app()) as client:
        nb = client.post("/api/notebooks", json={}).json()
        assert nb["title"] == "Untitled"
        assert nb["cells"] == []
