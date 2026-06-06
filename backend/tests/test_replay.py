from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.manifest import WORKSPACE_ENV


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv(WORKSPACE_ENV, str(tmp_path))
    # Keep the host kernel off so a Python cell is left untouched (the VM-gated path).
    monkeypatch.delenv("SMOLDUCK_IN_VM", raising=False)
    monkeypatch.delenv("SMOLDUCK_ALLOW_HOST_KERNEL", raising=False)
    return tmp_path


def _notebook_cells():
    return [
        {"kind": "markdown", "source": "# Report"},
        # A stale cached result that replay must overwrite with the live query.
        {"kind": "sql", "source": "SELECT 21 + 21 AS answer",
         "last_result": {"columns": [{"name": "answer"}], "rows": [[0]]}},
        {"kind": "chart", "source": "SELECT 1 AS x, 2 AS y", "config": {"type": "bar", "x": "x", "y": "y"}},
    ]


def test_replay_refreshes_sql_and_chart_results(workspace):
    with TestClient(create_app()) as client:
        nb = client.post("/api/notebooks", json={"title": "R", "cells": _notebook_cells()}).json()
        out = client.post(f"/api/notebooks/{nb['id']}/replay")
        assert out.status_code == 200
        body = out.json()
        by_kind = {c["kind"]: c for c in body["cells"]}
        # SQL cell re-ran: the stale [[0]] is replaced by the real answer.
        assert by_kind["sql"]["last_result"]["rows"] == [[42]]
        # Chart cell's query was re-run so its columns/rows are populated for rendering.
        assert by_kind["chart"]["last_result"]["columns"][0]["name"] == "x"
        assert by_kind["chart"]["last_result"]["rows"] == [[1, 2]]
        # updated_at advances.
        assert body["updated_at"] >= nb["updated_at"]


def test_replay_python_cell_left_untouched_off_vm(workspace):
    cells = [{"kind": "python", "source": "print('hi')",
              "last_result": {"stdout": "cached\n"}}]
    with TestClient(create_app()) as client:
        nb = client.post("/api/notebooks", json={"cells": cells}).json()
        body = client.post(f"/api/notebooks/{nb['id']}/replay").json()
        # Kernel is off the VM → the cached result is preserved, never executed on the host.
        assert body["cells"][0]["last_result"] == {"stdout": "cached\n"}


def test_replay_export_html(workspace):
    with TestClient(create_app()) as client:
        nb = client.post("/api/notebooks", json={"title": "Rep", "cells": _notebook_cells()}).json()
        out = client.post(f"/api/notebooks/{nb['id']}/replay?export=true")
        assert out.status_code == 200
        assert out.headers["content-type"].startswith("text/html")
        assert "<table" in out.text  # the refreshed SQL result rendered
        assert "42" in out.text


def test_replay_bad_sql_records_error(workspace):
    cells = [{"kind": "sql", "source": "SELECT * FROM nonexistent_table"}]
    with TestClient(create_app()) as client:
        nb = client.post("/api/notebooks", json={"cells": cells}).json()
        body = client.post(f"/api/notebooks/{nb['id']}/replay").json()
        assert body["cells"][0]["last_result"]["error"]


def test_replay_unknown_notebook_404(workspace):
    with TestClient(create_app()) as client:
        assert client.post("/api/notebooks/deadbeef/replay").status_code == 404
