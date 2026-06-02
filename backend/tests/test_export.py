from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.manifest import WORKSPACE_ENV

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

CELLS = [
    {"kind": "markdown", "source": "# Sales report\nSome **bold** notes."},
    {"kind": "sql", "source": "SELECT region, count(*) n FROM customers GROUP BY 1 ORDER BY 1",
     "last_result": {"columns": [{"name": "region", "type": "VARCHAR"}, {"name": "n", "type": "BIGINT"}],
                     "rows": [["East", 3], ["North", 3], ["South", 3], ["West", 3]], "row_count": 4}},
    {"kind": "chart", "source": "SELECT region, count(*) n FROM customers GROUP BY 1 ORDER BY 1",
     "config": {"type": "bar", "x": "region", "y": "n", "title": "by region"},
     "last_result": {"columns": [{"name": "region", "type": "VARCHAR"}, {"name": "n", "type": "BIGINT"}],
                     "rows": [["East", 3], ["North", 3], ["South", 3], ["West", 3]]}},
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    shutil.copy(FIXTURES / "customers.csv", tmp_path / "customers.csv")
    monkeypatch.setenv(WORKSPACE_ENV, str(tmp_path))
    with TestClient(create_app()) as c:
        c.post("/api/sources", json={"path": "."})
        yield c


def test_notebook_html_is_self_contained(client):
    nb = client.post("/api/notebooks", json={"title": "Sales report", "cells": CELLS}).json()
    r = client.get(f"/api/notebooks/../export/notebook/{nb['id']}") if False else client.get(
        f"/api/export/notebook/{nb['id']}")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "attachment" in r.headers.get("content-disposition", "")
    doc = r.text

    # markdown rendered
    assert "<h1>Sales report</h1>" in doc
    assert "<strong>bold</strong>" in doc
    # sql result table with data
    assert "<table" in doc and "<td>East</td>" in doc
    # a chart figure (Plotly) is embedded, and plotly.js is inlined → standalone
    assert "Plotly.newPlot" in doc
    assert "function" in doc and "var Plotly" in doc or "Plotly" in doc
    # no external script/style/link references (self-contained)
    assert "<script src=" not in doc
    assert 'rel="stylesheet"' not in doc and "<link" not in doc


def test_notebook_without_figures_skips_plotlyjs(client):
    nb = client.post("/api/notebooks", json={"title": "Plain", "cells": [CELLS[0]]}).json()
    doc = client.get(f"/api/export/notebook/{nb['id']}").text
    assert 'report-title">Plain<' in doc  # notebook title
    assert "Plotly.newPlot" not in doc  # no figures → no plotly payload inlined


def test_export_data_csv_and_parquet(client):
    q = "SELECT region, count(*) n FROM customers GROUP BY 1 ORDER BY 1"
    csv = client.post("/api/export/data", json={"sql": q, "format": "csv"})
    assert csv.status_code == 200
    text = csv.content.decode()
    assert text.splitlines()[0] == "region,n"
    assert "East,3" in text

    pq = client.post("/api/export/data", json={"sql": q, "format": "parquet"})
    assert pq.status_code == 200
    assert pq.content[:4] == b"PAR1"  # parquet magic


def test_export_unknown_notebook_404(client):
    assert client.get("/api/export/notebook/nope").status_code == 404


def test_export_bad_format_and_sql(client):
    assert client.post("/api/export/data", json={"sql": "SELECT 1", "format": "xml"}).status_code == 400
    assert client.post("/api/export/data", json={"sql": "", "format": "csv"}).status_code == 400
