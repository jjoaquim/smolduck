"""AI analyst: natural language → a reviewed SQL/Python cell.

Optional and BYO-key. The Anthropic key is read from `ANTHROPIC_API_KEY` at
request time and never persisted; with no key (and no fake flag) the feature is
disabled and `GET /api/agent/status` reports `enabled: false`, so the UI hides it.

The agent orchestration (the tool-use loop) runs in the backend — it needs the
key/endpoint and network. Its tools touch only the user's engine: `run_sql` on the
DuckDB connection and `run_python` in the **sandboxed kernel** (so agent-generated
code still only ever runs inside the VM). The agent never auto-runs anything: it
finishes by calling `propose_cell`, and the proposal is inserted review-first.

The model behind it is pluggable — Anthropic or Ollama (see providers.py); the
provider is the only model-specific seam. `_llm_call` dispatches to the active
provider and is the single function mocked in tests. A deterministic fake mode
(`SMOLDUCK_AGENT_FAKE=1`) drives the same proposal path with no network at all.
"""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .kernel import get_kernel
from .providers import ProviderError, get_provider, provider_info
from .sandbox import kernel_enabled
from .state import AppState, get_state

router = APIRouter(prefix="/api/agent", tags=["agent"])

MAX_STEPS = 8
SQL_PREVIEW_ROWS = 50

TOOLS = [
    {"name": "list_sources", "description": "List registered views with their columns and types.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_schema", "description": "Get the columns and types of one view.",
     "input_schema": {"type": "object", "properties": {"view": {"type": "string"}}, "required": ["view"]}},
    {"name": "run_sql", "description": "Run a read-only SQL query against DuckDB and see up to 50 rows. Use to explore and verify before proposing.",
     "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}},
    {"name": "run_python", "description": "Run Python in the sandboxed kernel (pandas as pd, polars as pl, numpy as np, plotly.express as px, and sql()). Use only when SQL can't express the analysis.",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "propose_cell", "description": "Propose the final notebook cell that answers the question. Call exactly once, after verifying your approach.",
     "input_schema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["sql", "python"]},
         "source": {"type": "string"},
         "explanation": {"type": "string"}}, "required": ["kind", "source"]}},
]

SYSTEM = (
    "You are smolduck's data analyst. You answer questions about the user's data by "
    "exploring it with the provided tools and then proposing a single notebook cell. "
    "Prefer a SQL cell; use a Python cell only when SQL can't express it. "
    "Verify your query with run_sql before proposing. Always finish by calling propose_cell "
    "with a correct, self-contained cell. Do not fabricate column names — check the schema."
)


class AskRequest(BaseModel):
    question: str


def fake_mode() -> bool:
    return os.environ.get("SMOLDUCK_AGENT_FAKE") == "1"


def agent_enabled() -> bool:
    return get_provider() is not None or fake_mode()


# --------------------------------------------------------------- tool helpers

def _describe(state: AppState, view: str) -> list[dict]:
    with state.db_lock:
        rows = state.db.execute(f'DESCRIBE "{view.replace(chr(34), "")}"').fetchall()
    return [{"name": r[0], "type": r[1]} for r in rows]


def _catalog(state: AppState) -> list[dict]:
    out = []
    for s in state.manifest.sources:
        try:
            cols = _describe(state, s.view_name)
        except Exception:
            cols = []
        out.append({"view": s.view_name, "kind": s.kind, "columns": cols})
    return out


def _run_sql(state: AppState, sql: str) -> str:
    try:
        with state.db_lock:
            rel = state.db.sql(sql)
            if rel is None:
                return json.dumps({"ok": True, "statement": True})
            cols = list(rel.columns)
            rows = rel.limit(SQL_PREVIEW_ROWS).fetchall()
        return json.dumps({"ok": True, "columns": cols,
                           "rows": [[_safe(v) for v in r] for r in rows], "row_count": len(rows)})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


def _safe(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    return str(v)


def _run_python(state: AppState, code: str) -> str:
    if not kernel_enabled():
        return json.dumps({"ok": False, "error": "python kernel is unavailable (VM-only)"})
    out = get_kernel(state).run_collect(code, timeout=60)
    return json.dumps({"ok": out.get("error") is None, "stdout": out.get("stdout", "")[:2000],
                       "result": out.get("result"), "error": out.get("error")})


def _dispatch(state: AppState, name: str, args: dict) -> str:
    if name == "list_sources":
        return json.dumps(_catalog(state))
    if name == "get_schema":
        return json.dumps(_describe(state, args.get("view", "")))
    if name == "run_sql":
        return _run_sql(state, args.get("sql", ""))
    if name == "run_python":
        return _run_python(state, args.get("code", ""))
    return json.dumps({"ok": False, "error": f"unknown tool {name}"})


# --------------------------------------------------------------- LLM seam

def _llm_call(messages: list, system: str, tools: list) -> dict:
    """Dispatch one turn to the active provider (mocked in tests).

    Takes the canonical block-format conversation and returns normalized blocks;
    the provider handles the model-specific wire translation (see providers.py).
    """
    provider = get_provider()
    if provider is None:  # defensive: routes gate on agent_enabled() first
        raise ProviderError("no LLM provider is configured")
    return provider.chat(messages, system, tools)


def _blocks_to_content(blocks: list) -> list:
    content = []
    for b in blocks:
        if b["type"] == "text":
            content.append({"type": "text", "text": b["text"]})
        elif b["type"] == "tool_use":
            content.append({"type": "tool_use", "id": b["id"], "name": b["name"], "input": b["input"]})
    return content


def _orchestrate(state: AppState, question: str, llm_call) -> dict:
    schema = json.dumps(_catalog(state))
    messages = [{"role": "user", "content": f"Catalog:\n{schema}\n\nQuestion: {question}"}]
    transcript = []

    for _ in range(MAX_STEPS):
        r = llm_call(messages, SYSTEM, TOOLS)
        blocks = r["blocks"]
        messages.append({"role": "assistant", "content": _blocks_to_content(blocks)})
        tool_uses = [b for b in blocks if b["type"] == "tool_use"]
        if not tool_uses:
            text = " ".join(b["text"] for b in blocks if b["type"] == "text").strip()
            return {"proposed_cell": None, "message": text or "(no proposal)", "transcript": transcript}

        results, proposal = [], None
        for tu in tool_uses:
            if tu["name"] == "propose_cell":
                proposal = tu["input"]
                results.append({"type": "tool_result", "tool_use_id": tu["id"],
                                "name": tu["name"], "content": "proposal recorded"})
            else:
                content = _dispatch(state, tu["name"], tu["input"] or {})
                transcript.append({"tool": tu["name"], "input": tu["input"]})
                results.append({"type": "tool_result", "tool_use_id": tu["id"],
                                "name": tu["name"], "content": content})
        messages.append({"role": "user", "content": results})
        if proposal is not None:
            return {"proposed_cell": {"kind": proposal.get("kind", "sql"), "source": proposal.get("source", ""),
                                      "explanation": proposal.get("explanation", "")},
                    "message": proposal.get("explanation", ""), "transcript": transcript}

    return {"proposed_cell": None, "message": "the analyst did not converge on a proposal", "transcript": transcript}


def _fake_orchestrate(state: AppState, question: str) -> dict:
    """Deterministic, network-free proposal: group-by-count on a mentioned column, else preview."""
    q = question.lower()
    for s in state.manifest.sources:
        try:
            cols = [c["name"] for c in _describe(state, s.view_name)]
        except Exception:
            continue
        for c in cols:
            if c.lower() in q:
                sql = f'SELECT "{c}", count(*) AS n FROM "{s.view_name}" GROUP BY 1 ORDER BY n DESC'
                return {"proposed_cell": {"kind": "sql", "source": sql,
                                          "explanation": f"Counts rows of {s.view_name} by {c}."},
                        "message": f"Counts rows of {s.view_name} by {c}.",
                        "transcript": [{"tool": "run_sql", "input": {"sql": sql}}]}
    if state.manifest.sources:
        v = state.manifest.sources[0].view_name
        sql = f'SELECT * FROM "{v}" LIMIT 50'
        return {"proposed_cell": {"kind": "sql", "source": sql, "explanation": f"Previews {v}."},
                "message": f"Previews {v}.", "transcript": [{"tool": "run_sql", "input": {"sql": sql}}]}
    return {"proposed_cell": None, "message": "No data sources are registered yet.", "transcript": []}


# --------------------------------------------------------------- routes

@router.get("/status")
def agent_status() -> dict:
    info = provider_info()
    fake = fake_mode() and info is None
    out = {"enabled": info is not None or fake, "fake": fake}
    if info:
        out.update(info)  # provider, model
    return out


@router.post("/ask")
def agent_ask(req: AskRequest, state: AppState = Depends(get_state)) -> dict:
    if not agent_enabled():
        raise HTTPException(
            status_code=403,
            detail="the AI analyst is disabled (set ANTHROPIC_API_KEY, or SMOLDUCK_LLM_PROVIDER=ollama).",
        )
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    if get_provider() is not None:
        try:
            return _orchestrate(state, question, _llm_call)
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _fake_orchestrate(state, question)
