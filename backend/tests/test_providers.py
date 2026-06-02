from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import providers
from app.main import create_app
from app.manifest import WORKSPACE_ENV
from app.providers import (
    AnthropicProvider,
    OllamaProvider,
    _canonical_to_ollama,
    _ollama_to_canonical,
    _tools_to_ollama,
    get_provider,
    provider_info,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

PROVIDER_ENV = [
    "ANTHROPIC_API_KEY", "SMOLDUCK_LLM_PROVIDER", "SMOLDUCK_AGENT_FAKE",
    "SMOLDUCK_OLLAMA_HOST", "OLLAMA_HOST", "SMOLDUCK_OLLAMA_MODEL", "SMOLDUCK_AGENT_MODEL",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in PROVIDER_ENV:
        monkeypatch.delenv(k, raising=False)


# ------------------------------------------------------------- selection

def test_no_config_means_no_provider():
    assert get_provider() is None and provider_info() is None


def test_anthropic_key_auto_selects(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    p = get_provider()
    assert isinstance(p, AnthropicProvider)
    assert provider_info() == {"provider": "anthropic", "model": "claude-opus-4-8"}


def test_explicit_ollama_needs_no_key(monkeypatch):
    monkeypatch.setenv("SMOLDUCK_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("SMOLDUCK_OLLAMA_MODEL", "qwen2.5-coder")
    p = get_provider()
    assert isinstance(p, OllamaProvider)
    assert provider_info() == {"provider": "ollama", "model": "qwen2.5-coder"}


def test_explicit_anthropic_without_key_is_disabled(monkeypatch):
    monkeypatch.setenv("SMOLDUCK_LLM_PROVIDER", "anthropic")
    assert get_provider() is None


def test_ollama_host_normalised(monkeypatch):
    monkeypatch.setenv("SMOLDUCK_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("SMOLDUCK_OLLAMA_HOST", "http://box:11434/")
    assert get_provider().host == "http://box:11434"


@pytest.mark.parametrize("given,expected", [
    # localhost can't be resolved inside the microVM; the IP is relayed to the
    # host's loopback by --outbound-localhost-only, so rewrite the name.
    ("http://localhost:11434", "http://127.0.0.1:11434"),
    ("http://localhost:11434/", "http://127.0.0.1:11434"),
    ("http://localhost", "http://127.0.0.1"),
    # a real hostname or an explicit IP is left alone
    ("http://box:11434", "http://box:11434"),
    ("http://127.0.0.1:11434", "http://127.0.0.1:11434"),
])
def test_ollama_localhost_rewritten_to_ip(monkeypatch, given, expected):
    monkeypatch.setenv("SMOLDUCK_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("SMOLDUCK_OLLAMA_HOST", given)
    assert get_provider().host == expected


def test_ollama_default_host_is_loopback_ip(monkeypatch):
    monkeypatch.setenv("SMOLDUCK_LLM_PROVIDER", "ollama")
    assert get_provider().host == "http://127.0.0.1:11434"


# ------------------------------------------------------------- translation

def test_tools_to_ollama_shape():
    tools = [{"name": "run_sql", "description": "run it",
              "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}}}}]
    out = _tools_to_ollama(tools)
    assert out[0]["type"] == "function"
    assert out[0]["function"]["name"] == "run_sql"
    assert out[0]["function"]["parameters"]["properties"]["sql"]["type"] == "string"


def test_canonical_to_ollama_flattens_turns():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "let me check"},
            {"type": "tool_use", "id": "t1", "name": "run_sql", "input": {"sql": "SELECT 1"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "name": "run_sql", "content": "{\"ok\":true}"}]},
    ]
    out = _canonical_to_ollama(messages, "SYS")
    assert out[0] == {"role": "system", "content": "SYS"}
    assert out[1] == {"role": "user", "content": "hi"}
    assert out[2]["role"] == "assistant"
    assert out[2]["tool_calls"][0]["function"] == {"name": "run_sql", "arguments": {"sql": "SELECT 1"}}
    # tool result becomes a `tool` message tagged with the tool name
    assert out[3] == {"role": "tool", "name": "run_sql", "content": "{\"ok\":true}"}


def test_ollama_to_canonical_parses_text_and_calls():
    data = {"message": {"content": "thinking", "tool_calls": [
        {"function": {"name": "run_sql", "arguments": {"sql": "SELECT 1"}}}]}}
    out = _ollama_to_canonical(data)
    assert out["stop_reason"] == "tool_use"
    assert out["blocks"][0] == {"type": "text", "text": "thinking"}
    tu = out["blocks"][1]
    assert tu["type"] == "tool_use" and tu["name"] == "run_sql" and tu["input"] == {"sql": "SELECT 1"}


def test_ollama_to_canonical_decodes_string_arguments():
    data = {"message": {"content": "", "tool_calls": [
        {"function": {"name": "x", "arguments": "{\"a\": 1}"}}]}}
    tu = _ollama_to_canonical(data)["blocks"][0]
    assert tu["input"] == {"a": 1}


def test_ollama_to_canonical_text_only_ends_turn():
    out = _ollama_to_canonical({"message": {"content": "done"}})
    assert out["stop_reason"] == "end_turn"
    assert out["blocks"] == [{"type": "text", "text": "done"}]


# --------------------------------------------- end-to-end via fake transport

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _ws(tmp_path, monkeypatch):
    shutil.copy(FIXTURES / "customers.csv", tmp_path / "customers.csv")
    monkeypatch.setenv(WORKSPACE_ENV, str(tmp_path))


def test_orchestration_through_ollama_provider(tmp_path, monkeypatch):
    """The full agent loop runs against Ollama's wire format via a fake httpx."""
    _ws(tmp_path, monkeypatch)
    monkeypatch.setenv("SMOLDUCK_LLM_PROVIDER", "ollama")

    seen = {"posts": [], "n": 0}

    def fake_post(url, json=None, timeout=None):
        seen["posts"].append(json)
        seen["n"] += 1
        if seen["n"] == 1:  # explore first
            return _FakeResp({"message": {"content": "", "tool_calls": [
                {"function": {"name": "run_sql", "arguments": {"sql": "SELECT count(*) FROM customers"}}}]}})
        return _FakeResp({"message": {"content": "", "tool_calls": [  # then propose
            {"function": {"name": "propose_cell", "arguments": {
                "kind": "sql", "source": "SELECT region, count(*) n FROM customers GROUP BY 1",
                "explanation": "counts by region"}}}]}})

    monkeypatch.setattr(httpx, "post", fake_post)
    with TestClient(create_app()) as c:
        assert c.get("/api/agent/status").json() == {
            "enabled": True, "fake": False, "provider": "ollama", "model": "llama3.1"}
        c.post("/api/sources", json={"path": "."})
        res = c.post("/api/agent/ask", json={"question": "by region?"}).json()
        assert res["proposed_cell"]["source"].startswith("SELECT region")
        assert any(t["tool"] == "run_sql" for t in res["transcript"])
    # The second request carried the tool result back as a `tool`-role message.
    second = seen["posts"][1]
    assert any(m["role"] == "tool" and m["name"] == "run_sql" for m in second["messages"])
    assert seen["n"] == 2


def test_ollama_transport_failure_surfaces_502(tmp_path, monkeypatch):
    _ws(tmp_path, monkeypatch)
    monkeypatch.setenv("SMOLDUCK_LLM_PROVIDER", "ollama")

    def boom(url, json=None, timeout=None):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", boom)
    with TestClient(create_app()) as c:
        c.post("/api/sources", json={"path": "."})
        r = c.post("/api/agent/ask", json={"question": "anything?"})
        assert r.status_code == 502 and "Ollama" in r.json()["detail"]
