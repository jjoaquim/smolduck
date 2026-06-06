"""Built-in demo dataset: one click to a non-empty workbench on an empty workspace.

The data is *generated in-code* (stdlib only, seeded) rather than copied from a
bundled file, so it works identically in the offline microVM — nothing is fetched
and nothing needs to be baked into the image. `POST /api/examples/load` writes the
CSV into the workspace and registers it as a view, exactly as if the user had
dropped the file in themselves.
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from . import sources
from .sandbox import in_vm
from .state import AppState, get_state

router = APIRouter(prefix="/api/examples", tags=["examples"])

EXAMPLES = {
    "sales": "A small e-commerce sales table — orders with channel, region, segment, amount, and date. Good for charts, EDA, and group-bys.",
}

_SEED = 20260604
_CHANNELS = ["web", "mobile", "store"]
_REGIONS = ["North", "South", "East", "West"]
_SEGMENTS = ["enterprise", "smb", "consumer"]
_SEGMENT_WEIGHTS = [0.18, 0.34, 0.48]


def _generate_sales(path: Path, n: int = 600) -> None:
    """Write a deterministic demo sales CSV (orders) to `path`."""
    rng = random.Random(_SEED)
    header = ["order_id", "order_date", "channel", "region", "segment", "units", "amount"]
    rows = []
    for i in range(1, n + 1):
        month = rng.randint(1, 12)
        day = rng.randint(1, 28)
        channel = rng.choice(_CHANNELS)
        region = rng.choice(_REGIONS)
        segment = rng.choices(_SEGMENTS, weights=_SEGMENT_WEIGHTS)[0]
        units = rng.randint(1, 12)
        # Enterprise orders skew larger; a touch of noise keeps distributions realistic.
        base = {"enterprise": 480.0, "smb": 140.0, "consumer": 45.0}[segment]
        amount = round(units * base * rng.uniform(0.6, 1.4), 2)
        rows.append([i, f"2026-{month:02d}-{day:02d}", channel, region, segment, units, amount])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


@router.get("")
def list_examples() -> dict:
    return {"examples": [{"name": k, "description": v} for k, v in EXAMPLES.items()]}


@router.post("/load")
def load_example(name: str = "sales", state: AppState = Depends(get_state)) -> dict:
    """Generate a built-in demo dataset into the workspace and register it."""
    if name not in EXAMPLES:
        raise HTTPException(status_code=404, detail=f"no such example: {name}")

    dest = state.workspace / f"{name}.csv"
    try:
        _generate_sales(dest)
    except OSError as exc:
        # A read-only workspace mount (`--readonly`) can't accept the demo file.
        hint = " (the workspace is mounted read-only)" if in_vm() else ""
        raise HTTPException(status_code=400, detail=f"could not write example data{hint}: {exc}") from exc

    try:
        source = sources._register_target(
            state,
            manifest_path=sources._manifest_path_for(state.workspace, dest),
            manifest_kind="csv",
            read_target=str(dest),
            requested_view=name,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"could not register example: {exc}") from exc
    state.save()
    return {"loaded": name, "source": source.model_dump()}
