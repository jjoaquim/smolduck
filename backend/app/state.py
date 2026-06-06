"""Process-wide application state: the workspace, its manifest, and the DB connection.

Kept in its own module so route modules can depend on it without importing
`main` (which would be circular).
"""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import Request

from . import db
from .manifest import Manifest, load_manifest, save_manifest


class AppState:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.manifest: Manifest = load_manifest(workspace)
        self.db = db.connect(workspace)
        # One DuckDB connection, but FastAPI runs sync endpoints in a threadpool,
        # so concurrent requests (e.g. the catalog firing several DESCRIBEs at
        # once) can hit the connection on different threads. The connection is
        # stateful and not safe for that — serialize all DB access through this.
        self.db_lock = threading.Lock()
        # The Python kernel subprocess (lazily created; only inside the VM). Typed
        # loosely to avoid importing kernel here.
        self.kernel = None
        # Attach the workspace's local DuckLake as the `lake` catalog (managed
        # tables + snapshots). Best-effort: disabled gracefully if the extension
        # or a writable workspace isn't available. Lazy import avoids a cycle
        # (lake's routes import this module).
        from . import lake
        self.lake = lake.attach_lake(self.db, self.workspace)

    def save(self) -> None:
        save_manifest(self.workspace, self.manifest)

    def close(self) -> None:
        if self.kernel is not None:
            self.kernel.shutdown()
        self.db.close()


def get_state(request: Request) -> AppState:
    return request.app.state.smol
