"""HTTP client for a running smolduck backend.

`smolduck run` writes ``<workspace>/.smolduck/session.json`` (see the ``Session``
type in ``cli/src/workspace.ts`` — fields ``port``, ``url``, …). We read ``port``
from it and reach the backend at ``http://127.0.0.1:<port>``; ``--url`` overrides
for native-backend dev/testing.

Everything stays on the loopback: the backend is **unauthenticated**, so this
server must only ever target a local session. A remote transport would need real
API auth first.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

SESSION_REL = ".smolduck/session.json"


class SmolduckError(RuntimeError):
    """A backend call failed, or no session is reachable."""


def discover_base_url(workspace: str | None, explicit_url: str | None = None) -> str:
    """Resolve the backend base URL from --url, else the workspace session file."""
    if explicit_url:
        return explicit_url.rstrip("/")
    ws = Path(workspace or ".").expanduser()
    session = ws / SESSION_REL
    if not session.exists():
        raise SmolduckError(
            f"no running smolduck session for '{ws}' (missing {session}). "
            f"Start one first:  smolduck run {ws}"
        )
    try:
        data = json.loads(session.read_text())
        port = int(data["port"])
    except Exception as exc:  # noqa: BLE001
        raise SmolduckError(f"could not read session file {session}: {exc}") from exc
    return f"http://127.0.0.1:{port}"


class SmolduckClient:
    """Thin typed wrapper over the smolduck HTTP API.

    Each method maps to one endpoint and returns parsed JSON (or raw bytes for the
    file-producing exports). HTTP errors become :class:`SmolduckError` carrying the
    backend's ``detail`` message, so tools can surface it cleanly to the agent.
    """

    def __init__(self, base_url: str, timeout: float = 180.0, *, transport: Any = None) -> None:
        self.base_url = base_url.rstrip("/")
        # `transport` is for tests (httpx.MockTransport); production uses the default.
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)

    # --------------------------------------------------------------- transport
    def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            resp = self._http.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise SmolduckError(
                f"cannot reach smolduck at {self.base_url} ({exc}). "
                f"Is the session still running?"
            ) from exc
        if resp.status_code >= 400:
            detail = resp.text
            try:
                detail = resp.json().get("detail", detail)
            except Exception:  # noqa: BLE001
                pass
            raise SmolduckError(f"{method} {path} → {resp.status_code}: {detail}")
        return resp

    def _json(self, method: str, path: str, **kw: Any) -> Any:
        return self._request(method, path, **kw).json()

    # ------------------------------------------------------------------- calls
    def health(self) -> dict:
        return self._json("GET", "/api/health")

    def list_sources(self) -> dict:
        return self._json("GET", "/api/sources")

    def register_source(self, path: str, view_name: str | None = None) -> dict:
        body: dict[str, Any] = {"path": path}
        if view_name:
            body["view_name"] = view_name
        return self._json("POST", "/api/sources", json=body)

    def profile_source(self, source_id: str) -> dict:
        return self._json("GET", f"/api/sources/{source_id}/profile")

    def query(self, sql: str, limit: int | None = None, offset: int = 0) -> dict:
        body: dict[str, Any] = {"sql": sql, "offset": offset}
        if limit is not None:
            body["limit"] = limit
        return self._json("POST", "/api/query", json=body)

    def kernel_status(self) -> dict:
        return self._json("GET", "/api/kernel/status")

    def kernel_exec(self, code: str, timeout: float | None = None) -> dict:
        body: dict[str, Any] = {"code": code}
        if timeout is not None:
            body["timeout"] = timeout
        return self._json("POST", "/api/kernel/exec", json=body)

    def create_notebook(self, title: str | None, cells: list[dict]) -> dict:
        body: dict[str, Any] = {"cells": cells}
        if title:
            body["title"] = title
        return self._json("POST", "/api/notebooks", json=body)

    def create_chart(self, query: str, config: dict, spec: dict, title: str | None = None) -> dict:
        body: dict[str, Any] = {"query": query, "config": config, "spec": spec}
        if title:
            body["title"] = title
        return self._json("POST", "/api/charts", json=body)

    def ml_experiment(
        self,
        source_id: str,
        features: list[str],
        target: str | None = None,
        task: str = "auto",
        test_size: float | None = None,
        k: int | None = None,
    ) -> dict:
        body: dict[str, Any] = {"source_id": source_id, "features": features, "task": task}
        if target is not None:
            body["target"] = target
        if test_size is not None:
            body["test_size"] = test_size
        if k is not None:
            body["k"] = k
        return self._json("POST", "/api/ml/experiments", json=body)

    def export_notebook(self, notebook_id: str) -> bytes:
        return self._request("GET", f"/api/export/notebook/{notebook_id}").content

    def export_data(self, sql: str, fmt: str = "csv") -> bytes:
        return self._request("POST", "/api/export/data", json={"sql": sql, "format": fmt}).content
