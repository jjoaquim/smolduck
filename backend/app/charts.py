"""Pinned charts: a saved Plotly spec + the query and encoding that produced it.

Per the data model, each chart is one `.smolduck/charts/<id>.json`
holding the originating SQL, the encoding config, the rendered Plotly spec, and a
title — a portable artifact independent of any notebook, so a pinned chart
survives relaunch (and feeds the notebook HTML export).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .manifest import smolduck_dir
from .state import AppState, get_state

router = APIRouter(prefix="/api/charts", tags=["charts"])

CHARTS_DIRNAME = "charts"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Chart(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = "Untitled chart"
    query: str = ""
    config: dict[str, Any] = Field(default_factory=dict)  # {type, x, y, color, ...}
    spec: dict[str, Any] = Field(default_factory=dict)  # Plotly {data, layout}
    created_at: str = Field(default_factory=_now_iso)


class ChartCreate(BaseModel):
    title: str | None = None
    query: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    spec: dict[str, Any] = Field(default_factory=dict)


def _charts_dir(state: AppState) -> Path:
    d = smolduck_dir(state.workspace) / CHARTS_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(state: AppState, chart_id: str) -> Path:
    if not _ID_RE.match(chart_id):
        raise HTTPException(status_code=400, detail="invalid chart id")
    return _charts_dir(state) / f"{chart_id}.json"


@router.get("")
def list_charts(state: AppState = Depends(get_state)) -> dict:
    charts: list[Chart] = []
    for p in _charts_dir(state).glob("*.json"):
        try:
            charts.append(Chart.model_validate_json(p.read_text()))
        except Exception:
            continue
    charts.sort(key=lambda c: c.created_at, reverse=True)
    return {"charts": [c.model_dump() for c in charts]}


@router.post("")
def create_chart(req: ChartCreate, state: AppState = Depends(get_state)) -> dict:
    chart = Chart(
        title=(req.title or "Untitled chart").strip() or "Untitled chart",
        query=req.query,
        config=req.config,
        spec=req.spec,
    )
    _path(state, chart.id).write_text(chart.model_dump_json(indent=2))
    return chart.model_dump()


@router.get("/{chart_id}")
def get_chart(chart_id: str, state: AppState = Depends(get_state)) -> dict:
    p = _path(state, chart_id)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"no such chart: {chart_id}")
    return Chart.model_validate_json(p.read_text()).model_dump()


@router.delete("/{chart_id}")
def delete_chart(chart_id: str, state: AppState = Depends(get_state)) -> dict:
    p = _path(state, chart_id)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"no such chart: {chart_id}")
    p.unlink()
    return {"deleted": chart_id}
