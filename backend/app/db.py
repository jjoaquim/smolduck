"""The single shared DuckDB connection bound to the workspace store.

One connection per process — it is stateful, which is why the backend runs a
single Uvicorn worker.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from .manifest import ensure_smolduck_dir

STORE_FILENAME = "store.duckdb"


def store_path(workspace: Path) -> Path:
    return ensure_smolduck_dir(workspace) / STORE_FILENAME


def connect(workspace: Path) -> duckdb.DuckDBPyConnection:
    """Open (or create) the workspace's persistent DuckDB database."""
    return duckdb.connect(str(store_path(workspace)))
