"""Headless notebook replay: re-run every cell server-side and refresh outputs.

Interactive execution is browser-driven (the workbench POSTs `/api/query` and
streams `/ws/kernel`). This module is the *non-interactive* path: load a saved
notebook, re-execute its cells in order, write the fresh results back, and
optionally render a self-contained HTML report — all with no browser. It turns a
notebook into a reproducible artifact (`smolduck replay`) and is the seam a future
scheduled-refresh would reuse.

Guardrail: like every code-execution path, Python cells run **only** through the
sandboxed kernel (`sandbox.kernel_enabled()`, VM-gated). Replay never executes
untrusted code on the host — when the kernel is unavailable a Python cell is left
untouched, keeping its cached result.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from . import export, lake, notebooks
from .kernel import get_kernel
from .query import QueryRequest, run_query
from .sandbox import kernel_enabled
from .state import AppState, get_state

router = APIRouter(prefix="/api/notebooks", tags=["notebooks"])

PY_REPLAY_TIMEOUT_S = 120.0


def _replay_query(state: AppState, source: str) -> dict | None:
    """Re-run a SQL/chart cell's query; return its result, or an error result.

    Returns None for an empty cell so its cached result is left as-is."""
    source = (source or "").strip()
    if not source:
        return None
    try:
        return run_query(QueryRequest(sql=source), state)
    except Exception as exc:  # noqa: BLE001 - HTTPException(detail=...) or otherwise
        detail = getattr(exc, "detail", None) or str(exc)
        return {"columns": [], "rows": [], "row_count": 0, "error": str(detail)}


def _run_cells(state: AppState, nb: notebooks.Notebook) -> None:
    """Re-execute each cell in order, refreshing its cached `last_result`.

    Markdown cells are inert; SQL and chart cells re-run their query; Python cells
    run in the sandboxed kernel (skipped, result kept, when the kernel is off)."""
    for cell in nb.cells:
        if cell.kind in ("sql", "chart"):
            result = _replay_query(state, cell.source)
            if result is not None:
                cell.last_result = result
        elif cell.kind == "python":
            if not kernel_enabled():
                continue  # never run untrusted code on the host; keep the cached result
            cell.last_result = get_kernel(state).run_collect(cell.source, timeout=PY_REPLAY_TIMEOUT_S)
        # markdown: nothing to run


def replay_notebook(state: AppState, notebook_id: str, reproduce: bool = False) -> notebooks.Notebook:
    """Re-run a notebook and persist its refreshed outputs.

    Default (HEAD): runs against current data and records the lake's HEAD snapshot
    on the notebook. With `reproduce` and a recorded `lake_snapshot`, the lake is
    re-attached read-only *as of* that snapshot for the duration, so managed-table
    reads reproduce exactly (writes to the lake would fail — use HEAD to rebuild)."""
    nb = notebooks._load(state, notebook_id)  # 404 if missing

    pinned = reproduce and lake.is_enabled(state) and nb.lake_snapshot is not None
    if pinned:
        with state.db_lock:
            lake.reattach_at(state.db, state.workspace, nb.lake_snapshot)
    try:
        _run_cells(state, nb)
    finally:
        if pinned:
            with state.db_lock:
                lake.reattach_head(state.db, state.workspace)

    # A HEAD run re-pins the notebook to the snapshot its fresh results came from.
    if not pinned and lake.is_enabled(state):
        with state.db_lock:
            nb.lake_snapshot = lake.current_snapshot(state.db)

    nb.updated_at = notebooks._now_iso()
    notebooks._save(state, nb)
    return nb


@router.post("/{notebook_id}/replay")
def replay(
    notebook_id: str,
    export_html: bool = Query(False, alias="export"),
    reproduce: bool = Query(False),
    state: AppState = Depends(get_state),
):
    """Re-run a notebook headless. Returns the refreshed notebook JSON, or the
    rendered HTML report when `?export=true`. With `?reproduce=true`, managed-table
    reads are pinned to the notebook's recorded DuckLake snapshot."""
    nb = replay_notebook(state, notebook_id, reproduce=reproduce)
    if export_html:
        body = export.render_notebook(nb)
        return Response(
            content=body,
            media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="{export._safe_filename(nb.title)}.html"'},
        )
    return nb.model_dump()
