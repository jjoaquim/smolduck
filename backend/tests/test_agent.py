from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import agent
from app.main import create_app
from app.manifest import WORKSPACE_ENV

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _ws(tmp_path, monkeypatch):
    shutil.copy(FIXTURES / "customers.csv", tmp_path / "customers.csv")
    monkeypatch.setenv(WORKSPACE_ENV, str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SMOLDUCK_AGENT_FAKE", raising=False)


def test_disabled_with_no_key(tmp_path, monkeypatch):
    _ws(tmp_path, monkeypatch)
    with TestClient(create_app()) as c:
        assert c.get("/api/agent/status").json()["enabled"] is False
        assert c.post("/api/agent/ask", json={"question": "hi"}).status_code == 403


def test_fake_mode_proposes_runnable_sql(tmp_path, monkeypatch):
    _ws(tmp_path, monkeypatch)
    monkeypatch.setenv("SMOLDUCK_AGENT_FAKE", "1")
    with TestClient(create_app()) as c:
        assert c.get("/api/agent/status").json()["enabled"] is True
        c.post("/api/sources", json={"path": "."})
        res = c.post("/api/agent/ask", json={"question": "which region has the most customers?"}).json()
        cell = res["proposed_cell"]
        assert cell["kind"] == "sql"
        assert "region" in cell["source"] and "GROUP BY" in cell["source"].upper()

        # The proposed cell actually runs and is correct.
        q = c.post("/api/query", json={"sql": cell["source"]}).json()
        assert {col["name"] for col in q["columns"]} >= {"region", "n"}
        assert q["row_count"] == 4 and all(r[1] == 3 for r in q["rows"])


def test_orchestration_loop_with_injected_llm(tmp_path, monkeypatch):
    _ws(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")  # take the real (non-fake) path

    calls = {"n": 0}

    def fake_llm(messages, system, tools):
        calls["n"] += 1
        if calls["n"] == 1:  # explore first
            return {"stop_reason": "tool_use", "blocks": [
                {"type": "tool_use", "id": "t1", "name": "run_sql",
                 "input": {"sql": "SELECT count(*) FROM customers"}}]}
        return {"stop_reason": "tool_use", "blocks": [  # then propose
            {"type": "tool_use", "id": "t2", "name": "propose_cell",
             "input": {"kind": "sql", "source": "SELECT region, count(*) n FROM customers GROUP BY 1",
                       "explanation": "counts by region"}}]}

    monkeypatch.setattr(agent, "_llm_call", fake_llm)
    with TestClient(create_app()) as c:
        c.post("/api/sources", json={"path": "."})
        res = c.post("/api/agent/ask", json={"question": "by region?"}).json()
        assert res["proposed_cell"]["source"].startswith("SELECT region")
        assert any(t["tool"] == "run_sql" for t in res["transcript"])
        assert calls["n"] == 2  # explored, then proposed


def test_status_reports_real_key(tmp_path, monkeypatch):
    _ws(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xyz")
    with TestClient(create_app()) as c:
        s = c.get("/api/agent/status").json()
        assert s["enabled"] is True and s["fake"] is False


def test_egress_offline_by_default(tmp_path, monkeypatch):
    _ws(tmp_path, monkeypatch)
    with TestClient(create_app()) as c:
        e = c.get("/api/agent/egress").json()
        assert e["policy"] == "offline" and e["allowed_hosts"] == []
        assert e["call_count"] == 0 and e["last_call_at"] is None


def test_egress_reports_anthropic_host(tmp_path, monkeypatch):
    _ws(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xyz")
    with TestClient(create_app()) as c:
        e = c.get("/api/agent/egress").json()
        assert e["policy"] == "allow-host" and e["allowed_hosts"] == ["api.anthropic.com"]
        assert e["provider"] == "anthropic"


def test_egress_log_counts_calls(tmp_path, monkeypatch):
    _ws(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def fake_llm(messages, system, tools):
        return {"stop_reason": "tool_use", "blocks": [
            {"type": "tool_use", "id": "t2", "name": "propose_cell",
             "input": {"kind": "sql", "source": "SELECT 1", "explanation": "x"}}]}

    monkeypatch.setattr(agent, "_llm_call", fake_llm)
    with TestClient(create_app()) as c:
        c.post("/api/sources", json={"path": "."})
        assert c.get("/api/agent/egress").json()["call_count"] == 0
        c.post("/api/agent/ask", json={"question": "anything?"})
        e = c.get("/api/agent/egress").json()
        assert e["call_count"] == 1 and e["last_call_at"] is not None
