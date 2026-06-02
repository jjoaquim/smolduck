from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.manifest import WORKSPACE_ENV

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _make_client(tmp_path, monkeypatch, *, allow: bool):
    shutil.copy(FIXTURES / "customers.csv", tmp_path / "customers.csv")
    monkeypatch.setenv(WORKSPACE_ENV, str(tmp_path))
    if allow:
        monkeypatch.setenv("SMOLDUCK_ALLOW_HOST_KERNEL", "1")
    else:
        monkeypatch.delenv("SMOLDUCK_ALLOW_HOST_KERNEL", raising=False)
    monkeypatch.delenv("SMOLDUCK_IN_VM", raising=False)
    return create_app()


@pytest.fixture
def client(tmp_path, monkeypatch):
    with TestClient(_make_client(tmp_path, monkeypatch, allow=True)) as c:
        c.post("/api/sources", json={"path": "."})
        yield c


def _exec(client, code, timeout=None):
    body = {"code": code}
    if timeout is not None:
        body["timeout"] = timeout
    resp = client.post("/api/kernel/exec", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_gating_off_by_default(tmp_path, monkeypatch):
    with TestClient(_make_client(tmp_path, monkeypatch, allow=False)) as c:
        status = c.get("/api/kernel/status").json()
        assert status["enabled"] is False
        assert c.post("/api/kernel/exec", json={"code": "1+1"}).status_code == 403
        assert c.post("/api/kernel/restart").status_code == 403


def test_status_enabled(client):
    assert client.get("/api/kernel/status").json()["enabled"] is True


def test_stdout_and_result(client):
    out = _exec(client, "print('hello')\n40 + 2")
    assert out["error"] is None
    assert out["stdout"] == "hello\n"
    assert out["result"] == "42"


def test_sql_helper_reads_view_into_dataframe(client):
    out = _exec(client, "df = sql('SELECT * FROM customers')\nprint(len(df))\ndf.shape[1]")
    assert out["error"] is None
    assert out["stdout"].strip() == "12"  # 12 customer rows
    assert out["result"] == "5"  # 5 columns


def test_dataframe_preview(client):
    out = _exec(client, "sql('SELECT * FROM customers')")
    assert out["error"] is None
    assert out["dataframe"] is not None
    assert out["dataframe"]["shape"] == [12, 5]
    assert "region" in out["dataframe"]["columns"]


def test_plotly_figure_inline(client):
    out = _exec(client, "px.bar(sql('SELECT region, count(*) n FROM customers GROUP BY 1'), x='region', y='n')")
    assert out["error"] is None
    assert len(out["figures"]) == 1
    assert "data" in out["figures"][0]


def test_sklearn_runs(client):
    code = (
        "from sklearn.linear_model import LinearRegression\n"
        "import numpy as np\n"
        "m = LinearRegression().fit(np.array([[1],[2],[3]]), np.array([2.,4.,6.]))\n"
        "round(float(m.coef_[0]), 1)"
    )
    out = _exec(client, code)
    assert out["error"] is None
    assert out["result"] == "2.0"


def test_persistent_namespace(client):
    assert _exec(client, "x = 41")["result"] is None
    assert _exec(client, "x + 1")["result"] == "42"


def test_error_is_reported(client):
    out = _exec(client, "raise ValueError('boom')")
    assert out["error"] is not None
    assert "boom" in out["error"]
    # kernel still usable after an error
    assert _exec(client, "1 + 1")["result"] == "2"


def test_timeout_kills_and_restarts(client):
    out = _exec(client, "while True:\n    pass", timeout=2)
    assert out["timed_out"] is True
    # a fresh kernel was spawned — namespace reset, but it works
    assert _exec(client, "7 * 6")["result"] == "42"


def test_restart_endpoint(client):
    _exec(client, "y = 99")
    assert client.post("/api/kernel/restart").json()["restarted"] is True
    out = _exec(client, "y")  # namespace cleared by restart
    assert out["error"] is not None and "y" in out["error"]
