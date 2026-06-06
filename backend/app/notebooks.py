"""Notebooks: ordered, multi-kind cells persisted as plain files.

Each notebook is one `.smolduck/notebooks/<id>.json` document holding an ordered
list of cells (sql / python / markdown / chart). Like the manifest, this is the
portable, git-friendly record — notebooks reconstruct from disk on relaunch with
no DB involvement. A cell may cache its `last_result` so reopening a workspace
shows the previous output immediately, before anything is re-run.

The Python kernel and chart builder execute cells; here a
cell is just durable content + a cached result.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .manifest import smolduck_dir
from .state import AppState, get_state

router = APIRouter(prefix="/api/notebooks", tags=["notebooks"])

NOTEBOOKS_DIRNAME = "notebooks"
CellKind = Literal["sql", "python", "markdown", "chart"]
_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_cell_id() -> str:
    return uuid.uuid4().hex[:8]


def _new_notebook_id() -> str:
    return uuid.uuid4().hex[:12]


class Cell(BaseModel):
    id: str = Field(default_factory=_new_cell_id)
    kind: CellKind = "sql"
    source: str = ""
    # Cached output for fast reopen (e.g. a query preview). Opaque to the store.
    last_result: Any | None = None
    # Cell-kind-specific settings, e.g. a chart cell's encoding {type, x, y, color}.
    config: dict[str, Any] | None = None


class Notebook(BaseModel):
    id: str = Field(default_factory=_new_notebook_id)
    title: str = "Untitled"
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    cells: list[Cell] = Field(default_factory=list)
    # DuckLake snapshot version this notebook's managed-table results correspond to
    # (set on replay). `smolduck replay --reproduce` re-attaches the lake as of this
    # version so the run reproduces exactly. None until first replayed with a lake.
    lake_snapshot: int | None = None


class CellInput(BaseModel):
    id: str | None = None
    kind: CellKind = "sql"
    source: str = ""
    last_result: Any | None = None
    config: dict[str, Any] | None = None


class NotebookCreate(BaseModel):
    title: str | None = None
    cells: list[CellInput] | None = None


class NotebookUpdate(BaseModel):
    title: str | None = None
    cells: list[CellInput] = Field(default_factory=list)


# ---------------------------------------------------------------- file store

def _notebooks_dir(state: AppState) -> Path:
    d = smolduck_dir(state.workspace) / NOTEBOOKS_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(state: AppState, notebook_id: str) -> Path:
    if not _ID_RE.match(notebook_id):
        raise HTTPException(status_code=400, detail="invalid notebook id")
    return _notebooks_dir(state) / f"{notebook_id}.json"


def _load(state: AppState, notebook_id: str) -> Notebook:
    p = _path(state, notebook_id)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"no such notebook: {notebook_id}")
    return Notebook.model_validate_json(p.read_text())


def _save(state: AppState, notebook: Notebook) -> None:
    p = _path(state, notebook.id)
    p.write_text(notebook.model_dump_json(indent=2))


def _cells_from_input(inputs: list[CellInput]) -> list[Cell]:
    """Preserve client order; mint ids for new cells."""
    return [
        Cell(id=c.id or _new_cell_id(), kind=c.kind, source=c.source,
             last_result=c.last_result, config=c.config)
        for c in inputs
    ]


def _summary(nb: Notebook) -> dict:
    return {
        "id": nb.id,
        "title": nb.title,
        "created_at": nb.created_at,
        "updated_at": nb.updated_at,
        "cell_count": len(nb.cells),
    }


# ------------------------------------------------------------------- routes

@router.get("")
def list_notebooks(state: AppState = Depends(get_state)) -> dict:
    """Summaries, newest-updated first."""
    notebooks: list[Notebook] = []
    for p in _notebooks_dir(state).glob("*.json"):
        try:
            notebooks.append(Notebook.model_validate_json(p.read_text()))
        except Exception:
            continue  # skip a corrupt/foreign file rather than fail the listing
    notebooks.sort(key=lambda n: n.updated_at, reverse=True)
    return {"notebooks": [_summary(n) for n in notebooks]}


@router.post("")
def create_notebook(req: NotebookCreate, state: AppState = Depends(get_state)) -> dict:
    cells = _cells_from_input(req.cells) if req.cells else []
    nb = Notebook(title=(req.title or "Untitled").strip() or "Untitled", cells=cells)
    _save(state, nb)
    return nb.model_dump()


@router.get("/{notebook_id}")
def get_notebook(notebook_id: str, state: AppState = Depends(get_state)) -> dict:
    return _load(state, notebook_id).model_dump()


@router.put("/{notebook_id}")
def update_notebook(
    notebook_id: str, req: NotebookUpdate, state: AppState = Depends(get_state)
) -> dict:
    nb = _load(state, notebook_id)  # 404 if missing — ids are server-minted
    if req.title is not None:
        nb.title = req.title.strip() or "Untitled"
    nb.cells = _cells_from_input(req.cells)
    nb.updated_at = _now_iso()
    _save(state, nb)
    return nb.model_dump()


@router.delete("/{notebook_id}")
def delete_notebook(notebook_id: str, state: AppState = Depends(get_state)) -> dict:
    p = _path(state, notebook_id)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"no such notebook: {notebook_id}")
    p.unlink()
    return {"deleted": notebook_id}
