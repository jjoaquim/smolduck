"""ML experiment runner: fit baseline models and log every run.

Runs the experiment in the sandboxed kernel subprocess, so — like the
kernel — it is gated to the VM. Each run is appended as one JSON line to
`.smolduck/experiments.jsonl` (the data model's experiment log); the full result
(metrics, feature importance, confusion matrix / residuals) is retrievable by id.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .kernel import get_kernel
from .manifest import smolduck_dir
from .sandbox import kernel_disabled_reason, kernel_enabled
from .state import AppState, get_state

router = APIRouter(prefix="/api/ml", tags=["ml"])
EXPERIMENTS_FILE = "experiments.jsonl"


class ExperimentRequest(BaseModel):
    source_id: str
    features: list[str]
    target: str | None = None
    task: str = "auto"  # auto | classification | regression | clustering
    test_size: float | None = None
    k: int | None = None


def _experiments_path(state: AppState) -> Path:
    return smolduck_dir(state.workspace) / EXPERIMENTS_FILE


def _require_kernel() -> None:
    if not kernel_enabled():
        raise HTTPException(status_code=403, detail=kernel_disabled_reason())


def _read_all(state: AppState) -> list[dict]:
    p = _experiments_path(state)
    if not p.exists():
        return []
    runs = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            runs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return runs


def _summary(run: dict) -> dict:
    return {
        "id": run["id"],
        "created_at": run["created_at"],
        "task": run.get("task"),
        "target": run.get("target"),
        "best_model": run.get("best_model"),
        "metric_primary": run.get("metric_primary"),
        "n_rows": run.get("n_rows"),
    }


@router.post("/experiments")
def create_experiment(req: ExperimentRequest, state: AppState = Depends(get_state)) -> dict:
    _require_kernel()
    source = next((s for s in state.manifest.sources if s.id == req.source_id), None)
    if source is None:
        raise HTTPException(status_code=404, detail=f"no such source: {req.source_id}")
    if not req.features:
        raise HTTPException(status_code=400, detail="select at least one feature")

    spec = {
        "view_name": source.view_name,
        "features": req.features,
        "target": req.target,
        "task": req.task,
        "test_size": req.test_size,
        "k": req.k,
    }
    res = get_kernel(state).run_ml(spec)
    if res.get("error"):
        raise HTTPException(status_code=400, detail=res["error"])
    if not res.get("result"):
        raise HTTPException(status_code=400, detail="experiment produced no result")

    run = {
        "id": uuid.uuid4().hex[:12],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_id": req.source_id,
        "elapsed_ms": res.get("elapsed_ms"),
        **res["result"],
    }
    path = _experiments_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(run) + "\n")
    return run


@router.get("/experiments")
def list_experiments(state: AppState = Depends(get_state)) -> dict:
    runs = _read_all(state)
    runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return {"experiments": [_summary(r) for r in runs]}


@router.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str, state: AppState = Depends(get_state)) -> dict:
    for run in _read_all(state):
        if run.get("id") == experiment_id:
            return run
    raise HTTPException(status_code=404, detail=f"no such experiment: {experiment_id}")
