"""Query history: a small, per-workspace log of SQL the user has run.

Persisted like the other artifacts — one plain file, `.smolduck/history.json` —
so recent queries survive relaunch and feed the command palette. Newest first,
capped, and consecutive re-runs of the same SQL collapse into one entry (so
hammering ⌘⏎ doesn't bury the history).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .manifest import smolduck_dir
from .state import AppState, get_state

router = APIRouter(prefix="/api/history", tags=["history"])

HISTORY_FILENAME = "history.json"
MAX_ENTRIES = 200


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class HistoryEntry(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    sql: str
    executed_at: str = Field(default_factory=_now_iso)
    elapsed_ms: float | None = None
    row_count: int | None = None
    ok: bool = True


class HistoryAdd(BaseModel):
    sql: str
    elapsed_ms: float | None = None
    row_count: int | None = None
    ok: bool = True


def _path(state: AppState):
    return smolduck_dir(state.workspace) / HISTORY_FILENAME


def _load(state: AppState) -> list[dict]:
    p = _path(state)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text()).get("entries", [])
    except Exception:  # noqa: BLE001 - a corrupt/foreign file shouldn't break the app
        return []


def _save(state: AppState, entries: list[dict]) -> None:
    p = _path(state)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"entries": entries}, indent=2))


@router.get("")
def list_history(limit: int = 50, state: AppState = Depends(get_state)) -> dict:
    """Recent queries, newest first (capped at `limit`)."""
    return {"history": _load(state)[: max(0, limit)]}


@router.post("")
def add_history(req: HistoryAdd, state: AppState = Depends(get_state)) -> dict:
    sql = req.sql.strip()
    if not sql:
        raise HTTPException(status_code=400, detail="sql is required")
    entries = _load(state)
    entry = HistoryEntry(
        sql=sql, elapsed_ms=req.elapsed_ms, row_count=req.row_count, ok=req.ok
    ).model_dump()
    # Collapse a consecutive re-run of the same SQL into the newest entry.
    if entries and entries[0].get("sql") == sql:
        entries[0] = entry
    else:
        entries.insert(0, entry)
    _save(state, entries[:MAX_ENTRIES])
    return entry


@router.delete("")
def clear_history(state: AppState = Depends(get_state)) -> dict:
    _save(state, [])
    return {"cleared": True}
