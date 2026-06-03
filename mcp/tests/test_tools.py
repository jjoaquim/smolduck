"""Unit tests: session discovery, client→endpoint mapping, and tool error handling.

No real backend — an httpx.MockTransport records each request and returns canned
responses, so we assert the method+path+body each tool produces.
"""

from __future__ import annotations

import json

import httpx
import pytest

from smolduck_mcp import client as clientmod
from smolduck_mcp import server
from smolduck_mcp.client import SmolduckClient, SmolduckError, discover_base_url


# --------------------------------------------------------------- session discovery

def test_discover_url_override_wins(tmp_path):
    assert discover_base_url(str(tmp_path), "http://127.0.0.1:9999/") == "http://127.0.0.1:9999"


def test_discover_reads_port_from_session(tmp_path):
    sd = tmp_path / ".smolduck"
    sd.mkdir()
    (sd / "session.json").write_text(json.dumps({"port": 4321, "url": "http://127.0.0.1:4321/?t=x"}))
    assert discover_base_url(str(tmp_path)) == "http://127.0.0.1:4321"


def test_discover_missing_session_is_friendly(tmp_path):
    with pytest.raises(SmolduckError) as e:
        discover_base_url(str(tmp_path))
    assert "smolduck run" in str(e.value)  # actionable hint, not a stack trace


# ------------------------------------------------------------- client → endpoint map

def _client(handler):
    return SmolduckClient("http://127.0.0.1:4290", transport=httpx.MockTransport(handler))


def _seen(handler_box, status=200, payload=None, content=None):
    def handler(request: httpx.Request) -> httpx.Response:
        handler_box["method"] = request.method
        handler_box["path"] = request.url.path
        handler_box["body"] = json.loads(request.content) if request.content else None
        if content is not None:
            return httpx.Response(status, content=content)
        return httpx.Response(status, json=payload if payload is not None else {})
    return handler


def test_query_posts_sql():
    box = {}
    c = _client(_seen(box, payload={"columns": [], "rows": []}))
    c.query("SELECT 1", limit=50)
    assert box["method"] == "POST" and box["path"] == "/api/query"
    assert box["body"] == {"sql": "SELECT 1", "offset": 0, "limit": 50}


def test_register_source_omits_blank_view_name():
    box = {}
    c = _client(_seen(box, payload={"registered": []}))
    c.register_source("data.csv")
    assert box["path"] == "/api/sources" and box["body"] == {"path": "data.csv"}


def test_kernel_exec_maps():
    box = {}
    c = _client(_seen(box, payload={"stdout": "ok", "error": None}))
    c.kernel_exec("print(1)", timeout=5)
    assert box["path"] == "/api/kernel/exec"
    assert box["body"] == {"code": "print(1)", "timeout": 5}


def test_ml_experiment_maps_required_and_optional():
    box = {}
    c = _client(_seen(box, payload={"id": "x"}))
    c.ml_experiment("src1", ["a", "b"], target="y", k=3)
    assert box["path"] == "/api/ml/experiments"
    assert box["body"] == {"source_id": "src1", "features": ["a", "b"], "task": "auto", "target": "y", "k": 3}


def test_export_data_returns_bytes():
    c = _client(_seen({}, content=b"a,b\n1,2\n"))
    assert c.export_data("SELECT 1", "csv") == b"a,b\n1,2\n"


def test_http_error_carries_detail():
    c = _client(_seen({}, status=403, payload={"detail": "kernel disabled"}))
    with pytest.raises(SmolduckError) as e:
        c.kernel_exec("x")
    assert "kernel disabled" in str(e.value)


# ------------------------------------------------------------------ server tools

def test_tool_returns_error_when_no_session(monkeypatch):
    # No client configured / discovery fails → tool body returns a clean error dict.
    server._client = None
    server._cfg["workspace"], server._cfg["url"] = "/nonexistent-ws", None
    out = server.list_sources()
    assert "error" in out and "smolduck run" in out["error"]


def test_tool_uses_injected_client(monkeypatch):
    box = {}
    server._client = _client(_seen(box, payload={"sources": [{"id": "s1"}]}))
    try:
        out = server.list_sources()
        assert out == {"sources": [{"id": "s1"}]} and box["path"] == "/api/sources"
        server.get_schema('my"view')
        assert box["path"] == "/api/query" and 'DESCRIBE "my""view"' in box["body"]["sql"]
    finally:
        server._client = None
