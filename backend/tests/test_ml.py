from __future__ import annotations

import csv
import random
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.manifest import WORKSPACE_ENV


def _write_synthetic(path: Path) -> None:
    # y ≈ 2*x1 (strong linear signal); label = x1 > 50 (perfectly separable by x1).
    random.seed(0)
    with (path / "synth.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["x1", "x2", "y", "label"])
        for i in range(300):
            x1 = i % 100
            w.writerow([x1, round(random.random(), 4), round(2.0 * x1 + 5 + random.uniform(-1, 1), 3),
                        "high" if x1 > 50 else "low"])


def _make_app(tmp_path, monkeypatch, *, allow: bool):
    _write_synthetic(tmp_path)
    monkeypatch.setenv(WORKSPACE_ENV, str(tmp_path))
    monkeypatch.delenv("SMOLDUCK_IN_VM", raising=False)
    if allow:
        monkeypatch.setenv("SMOLDUCK_ALLOW_HOST_KERNEL", "1")
    else:
        monkeypatch.delenv("SMOLDUCK_ALLOW_HOST_KERNEL", raising=False)
    return create_app()


@pytest.fixture
def client(tmp_path, monkeypatch):
    with TestClient(_make_app(tmp_path, monkeypatch, allow=True)) as c:
        c.post("/api/sources", json={"path": "synth.csv"})
        yield c


def _metric(run, model_name, key):
    return next(m["metrics"][key] for m in run["models"] if m["name"] == model_name)


def test_gating_off_by_default(tmp_path, monkeypatch):
    with TestClient(_make_app(tmp_path, monkeypatch, allow=False)) as c:
        c.post("/api/sources", json={"path": "synth.csv"})
        r = c.post("/api/ml/experiments", json={"source_id": "synth", "target": "y", "features": ["x1"]})
        assert r.status_code == 403


def test_regression_beats_baseline_and_is_sane(client):
    r = client.post("/api/ml/experiments",
                    json={"source_id": "synth", "target": "y", "features": ["x1", "x2"], "task": "regression"})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["task"] == "regression"
    best_r2 = _metric(run, run["best_model"], "r2")
    base_r2 = _metric(run, "baseline (mean)", "r2")
    assert best_r2 > 0.9          # strong linear signal → high r2
    assert best_r2 > base_r2      # beats the trivial mean baseline
    assert run["feature_importance"] and run["feature_importance"][0]["feature"] == "x1"
    assert run["residuals"] is not None


def test_classification_beats_baseline_and_logs(client):
    r = client.post("/api/ml/experiments",
                    json={"source_id": "synth", "target": "label", "features": ["x1"], "task": "classification"})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["task"] == "classification"
    best_acc = _metric(run, run["best_model"], "accuracy")
    base_acc = _metric(run, "baseline (most frequent)", "accuracy")
    assert best_acc > 0.9
    assert best_acc >= base_acc
    cm = run["confusion_matrix"]
    assert set(cm["labels"]) == {"high", "low"}

    # logged to experiments.jsonl and retrievable
    listed = client.get("/api/ml/experiments").json()["experiments"]
    assert any(e["id"] == run["id"] for e in listed)
    got = client.get(f"/api/ml/experiments/{run['id']}").json()
    assert got["best_model"] == run["best_model"]
    jsonl = Path(client.app.state.smol.workspace) / ".smolduck" / "experiments.jsonl"
    assert jsonl.exists() and len(jsonl.read_text().strip().splitlines()) >= 1


def test_auto_task_inference(client):
    # numeric continuous target → regression
    r = client.post("/api/ml/experiments", json={"source_id": "synth", "target": "y", "features": ["x1"]})
    assert r.json()["task"] == "regression"
    # string target → classification
    r = client.post("/api/ml/experiments", json={"source_id": "synth", "target": "label", "features": ["x1"]})
    assert r.json()["task"] == "classification"


def test_unknown_source_404(client):
    assert client.post("/api/ml/experiments",
                       json={"source_id": "nope", "target": "y", "features": ["x1"]}).status_code == 404
