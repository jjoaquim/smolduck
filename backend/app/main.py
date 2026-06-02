"""FastAPI application: health, workspace manifest, and static frontend serving.

Native-first: this runs directly under `uvicorn` against a local workspace
folder during native development. The microVM wraps this unchanged.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from . import agent, charts, export, kernel_api, ml, notebooks, query, sources
from .manifest import SCHEMA_VERSION, ensure_smolduck_dir, manifest_path, resolve_workspace_dir
from .state import AppState, get_state


def _register_mime_types() -> None:
    """Register MIME types so static assets serve correctly even when the host has
    no mime database (e.g. the minimal python:3.12-slim VM image). Without this,
    `mimetypes.guess_type` returns None and Starlette serves `.js` as text/html,
    which browsers refuse for ES modules."""
    for ext, ctype in {
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".css": "text/css",
        ".html": "text/html",
        ".json": "application/json",
        ".svg": "image/svg+xml",
        ".woff2": "font/woff2",
        ".woff": "font/woff",
        ".wasm": "application/wasm",
    }.items():
        mimetypes.add_type(ctype, ext)


_register_mime_types()


class RevalidateStaticFiles(StaticFiles):
    """StaticFiles that forces revalidation on every asset.

    The SPA loads ES modules by stable path (`./app.js` → `CellEditor.js` …) with
    no content hash, so a browser is free to keep serving an old copy after the VM
    image is rebuilt — which silently runs stale frontend code. `Cache-Control:
    no-cache` makes the browser revalidate against the ETag every load: unchanged
    files still return a cheap 304, but a rebuilt asset is fetched fresh. Without a
    build step we can't hash filenames, so this is the no-build equivalent."""

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


def _frontend_dir() -> Path:
    override = os.environ.get("SMOLDUCK_FRONTEND_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "frontend"


def _compute_build_id() -> str:
    """Identity of the frontend bundle currently on disk.

    The SPA loads ES modules once into a tab's memory, so a tab opened before an
    image rebuild keeps running the old code even with no-cache headers (those
    only affect the *next* load). The client polls this id and reloads when it
    changes, so open tabs self-heal after a rebuild. Built from each asset's
    path + mtime + size: stable across plain restarts of the same image, but
    changes whenever the baked frontend does."""
    frontend = _frontend_dir()
    if not frontend.is_dir():
        return SCHEMA_VERSION
    h = hashlib.sha256()
    for p in sorted(frontend.rglob("*")):
        if p.is_file():
            st = p.stat()
            h.update(p.relative_to(frontend).as_posix().encode())
            h.update(f"\0{st.st_mtime_ns}\0{st.st_size}\0".encode())
    return h.hexdigest()[:12]


_BUILD_ID = _compute_build_id()


@asynccontextmanager
async def lifespan(app: FastAPI):
    workspace = resolve_workspace_dir()
    ensure_smolduck_dir(workspace)
    first_run = not manifest_path(workspace).exists()
    state = AppState(workspace)
    app.state.smol = state
    sources.reconcile_views(state)
    # First launch on a fresh workspace: register the data files so the workbench
    # opens with all sources ready (acceptance criterion).
    if first_run and not state.manifest.sources:
        sources.autoregister(state)
    try:
        yield
    finally:
        state.close()


def create_app() -> FastAPI:
    app = FastAPI(title="smolduck", version=SCHEMA_VERSION, lifespan=lifespan)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "service": "smolduck", "version": SCHEMA_VERSION, "build": _BUILD_ID}

    @app.get("/api/workspace")
    def workspace(state: AppState = Depends(get_state)) -> dict:
        return {
            "workspace": str(state.workspace),
            "manifest": state.manifest.model_dump(),
        }

    app.include_router(sources.router)
    app.include_router(query.router)
    app.include_router(notebooks.router)
    app.include_router(kernel_api.router)
    app.include_router(charts.router)
    app.include_router(ml.router)
    app.include_router(agent.router)
    app.include_router(export.router)

    # Mount the static frontend last so /api/* routes take precedence.
    frontend = _frontend_dir()
    if frontend.is_dir():
        app.mount("/", RevalidateStaticFiles(directory=str(frontend), html=True), name="frontend")

    return app


app = create_app()
