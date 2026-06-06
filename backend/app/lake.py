"""A local DuckLake attached to the workspace — the home for *managed* tables.

smolduck reads the user's raw files as views (see sources.py); those are
unchanged. This module adds a second catalog, `lake`, backed by a DuckLake whose
metadata is a local DuckDB file and whose data is local Parquet — both under
`.smolduck/`, so they persist across the disposable VM exactly like `store.duckdb`
and never need the network.

Why: every write to a DuckLake table creates a **snapshot**, so a notebook can
record the version it ran against and `smolduck replay --reproduce` can re-attach
the lake *as of* that snapshot and reproduce its managed-table results exactly
(see replay.py). Raw file views still reflect current file contents — to freeze
those, materialize them into the lake.

The lake is attached on the one shared connection (held under `state.db_lock`)
alongside `store.duckdb`; no `USE`, so existing unqualified DDL is unaffected and
managed tables are addressed as `lake.<name>`. Attaching is best-effort: a missing
extension (native dev) or a read-only workspace simply leaves the lake disabled,
and the rest of smolduck works as before.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .manifest import ensure_smolduck_dir, smolduck_dir
from .state import AppState, get_state

router = APIRouter(prefix="/api/lake", tags=["lake"])

LAKE_ALIAS = "lake"
LAKE_CATALOG = "lake.ducklake"
LAKE_DATA = "lake_files"
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def disabled_by_env() -> bool:
    return os.environ.get("SMOLDUCK_LAKE", "1") == "0"


def catalog_path(workspace: Path) -> Path:
    return smolduck_dir(workspace) / LAKE_CATALOG


def data_dir(workspace: Path) -> Path:
    return smolduck_dir(workspace) / LAKE_DATA


def _attach_sql(workspace: Path, snapshot_version: int | None = None) -> str:
    cat = str(catalog_path(workspace)).replace("'", "''")
    data = str(data_dir(workspace)).replace("'", "''")
    opts = [f"DATA_PATH '{data}'"]
    if snapshot_version is not None:
        opts.append(f"SNAPSHOT_VERSION {int(snapshot_version)}")
    return f"ATTACH 'ducklake:{cat}' AS {LAKE_ALIAS} ({', '.join(opts)})"


def _load_extension(con) -> None:
    # LOAD first: in the offline VM the extension is baked in at image time, so
    # this succeeds without network. INSTALL is the native-dev fallback only.
    try:
        con.execute("LOAD ducklake;")
        return
    except Exception:
        con.execute("INSTALL ducklake; LOAD ducklake;")


def attach_lake(con, workspace: Path) -> dict:
    """Attach the workspace DuckLake as `lake`. Never raises — returns a status
    dict; a missing extension or read-only workspace just disables the lake."""
    if disabled_by_env():
        return {"enabled": False, "reason": "disabled via SMOLDUCK_LAKE=0"}
    try:
        ensure_smolduck_dir(workspace)
        data_dir(workspace).mkdir(parents=True, exist_ok=True)
        _load_extension(con)
        con.execute(_attach_sql(workspace))
        return {"enabled": True, "reason": None}
    except Exception as exc:  # noqa: BLE001 - degrade gracefully, never block boot
        return {"enabled": False, "reason": str(exc)[:200]}


def is_enabled(state: AppState) -> bool:
    status = getattr(state, "lake", None)
    return bool(status and status.get("enabled"))


def current_snapshot(con) -> int | None:
    """The latest snapshot version of the lake (an int), or None if empty.
    Fetches only the integer id — never the tz timestamp (which needs pytz)."""
    row = con.execute(f"SELECT max(snapshot_id) FROM {LAKE_ALIAS}.snapshots()").fetchone()
    return int(row[0]) if row and row[0] is not None else None


def reattach_at(con, workspace: Path, version: int) -> None:
    """Re-attach the lake read-only as of `version` (caller holds db_lock)."""
    con.execute(f"DETACH {LAKE_ALIAS}")
    con.execute(_attach_sql(workspace, snapshot_version=version))


def reattach_head(con, workspace: Path) -> None:
    """Re-attach the lake at HEAD, read-write (caller holds db_lock)."""
    con.execute(f"DETACH {LAKE_ALIAS}")
    con.execute(_attach_sql(workspace))


class MaterializeRequest(BaseModel):
    table: str
    sql: str


# ------------------------------------------------------------------- routes

@router.get("/status")
def lake_status(state: AppState = Depends(get_state)) -> dict:
    enabled = is_enabled(state)
    out: dict = {"enabled": enabled, "reason": (getattr(state, "lake", None) or {}).get("reason")}
    if enabled:
        with state.db_lock:
            out["data_path"] = str(data_dir(state.workspace))
            out["current_snapshot"] = current_snapshot(state.db)
    return out


@router.get("/snapshots")
def lake_snapshots(state: AppState = Depends(get_state)) -> dict:
    if not is_enabled(state):
        raise HTTPException(status_code=400, detail="DuckLake is not enabled for this workspace")
    with state.db_lock:
        rows = state.db.execute(
            f"SELECT snapshot_id, snapshot_time::VARCHAR, schema_version "
            f"FROM {LAKE_ALIAS}.snapshots() ORDER BY snapshot_id"
        ).fetchall()
    return {"snapshots": [{"version": r[0], "time": r[1], "schema_version": r[2]} for r in rows]}


@router.post("/materialize")
def lake_materialize(req: MaterializeRequest, state: AppState = Depends(get_state)) -> dict:
    """Materialize a query as a managed lake table (creating a new snapshot)."""
    if not is_enabled(state):
        raise HTTPException(status_code=400, detail="DuckLake is not enabled for this workspace")
    table = req.table.strip()
    if not _IDENT_RE.match(table):
        raise HTTPException(status_code=400, detail="table must be a simple identifier")
    sql = req.sql.strip().rstrip(";").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="sql is required")
    with state.db_lock:
        try:
            state.db.execute(f'CREATE OR REPLACE TABLE {LAKE_ALIAS}."{table}" AS ({sql})')
            version = current_snapshot(state.db)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"materialize failed: {exc}") from exc
    return {"table": table, "snapshot": version}
