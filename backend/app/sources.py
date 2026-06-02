"""Source registration: turn files, folders, and remote URIs into DuckDB views.

The user's data stays in DuckDB; the manifest records only *where* each view
came from (relative path when inside the workspace, else absolute, or the remote
URI). Views are rebuilt from the manifest on startup so a workspace reconstructs
from disk on relaunch — even if mounted at a different path (e.g. in the VM).
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

import duckdb
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .manifest import Source
from .profile import profile_view
from .state import AppState, get_state

router = APIRouter(prefix="/api/sources", tags=["sources"])

REMOTE_SCHEMES = ("http://", "https://", "s3://", "gs://", "gcs://", "az://", "azure://", "r2://", "hf://")
EXT_KIND = {
    ".csv": "csv",
    ".tsv": "csv",
    ".parquet": "parquet",
    ".pq": "parquet",
    ".json": "json",
    ".ndjson": "json",
    ".jsonl": "json",
    ".xlsx": "xlsx",
    ".xls": "xlsx",
}
# Lazily-loaded DuckDB extensions, keyed by the kind that needs them.
_EXTENSION_FOR_KIND = {"xlsx": "excel", "remote": "httpfs"}


class RegisterRequest(BaseModel):
    path: str
    view_name: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_remote(raw: str) -> bool:
    return raw.startswith(REMOTE_SCHEMES)


def _file_kind(name: str) -> str | None:
    return EXT_KIND.get(Path(name).suffix.lower())


def _sanitize_identifier(name: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()
    if not s:
        s = "src"
    if s[0].isdigit():
        s = f"t_{s}"
    return s


def _unique_view_name(base: str, taken: set[str]) -> str:
    name = base
    i = 2
    while name in taken:
        name = f"{base}_{i}"
        i += 1
    return name


def _ensure_extension(con: duckdb.DuckDBPyConnection, name: str) -> None:
    # LOAD first: in the no-egress VM the extension is pre-installed at image-bake
    # time, so LOAD succeeds without network. Only fall back to INSTALL (which may
    # hit the network) when it isn't already present — e.g. native dev.
    try:
        con.execute(f"LOAD {name};")
        return
    except Exception:
        pass
    try:
        con.execute(f"INSTALL {name}; LOAD {name};")
    except Exception as exc:  # offline and not pre-installed
        raise HTTPException(
            status_code=502,
            detail=f"could not load DuckDB extension '{name}': {exc}",
        ) from exc


def _read_expr(file_kind: str, abspath_or_uri: str) -> str:
    p = abspath_or_uri.replace("'", "''")
    if file_kind == "csv":
        return f"read_csv_auto('{p}')"
    if file_kind == "parquet":
        return f"read_parquet('{p}')"
    if file_kind == "json":
        return f"read_json_auto('{p}')"
    if file_kind == "xlsx":
        return f"read_xlsx('{p}', header = true)"
    raise HTTPException(status_code=400, detail=f"unsupported file kind: {file_kind}")


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _walk_supported(root: Path) -> list[Path]:
    """Every supported file under root, skipping hidden entries and `.smolduck/`."""
    found: list[Path] = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if p.is_file() and _file_kind(p.name):
            found.append(p)
    return found


def _create_view(state: AppState, view_name: str, manifest_kind: str, read_target: str) -> None:
    """(Re)create one DuckDB view. `read_target` is an absolute path or remote URI."""
    with state.db_lock:  # serialize DDL against concurrent queries on the shared connection
        if manifest_kind == "remote":
            _ensure_extension(state.db, "httpfs")
            file_kind = _file_kind(read_target) or "csv"
        else:
            file_kind = manifest_kind
            ext = _EXTENSION_FOR_KIND.get(manifest_kind)
            if ext:
                _ensure_extension(state.db, ext)
        expr = _read_expr(file_kind, read_target)
        state.db.execute(f"CREATE OR REPLACE VIEW {_quote_ident(view_name)} AS SELECT * FROM {expr}")


def _read_target(state: AppState, source: Source) -> str:
    """Resolve a manifest source's stored path to something DuckDB can read."""
    if source.kind == "remote":
        return source.path
    p = Path(source.path)
    if not p.is_absolute():
        p = state.workspace / p
    return str(p.resolve())


def autoregister(state: AppState) -> list[Source]:
    """Best-effort: register every supported file under the workspace.

    Called on first launch so `smolduck run ./data` opens with all files already
    registered as queryable views (per the acceptance criteria). Idempotent and
    silent on per-file failure.
    """
    files = _walk_supported(state.workspace)
    registered: list[Source] = []
    for f in files:
        try:
            registered.append(
                _register_target(
                    state,
                    manifest_path=_manifest_path_for(state.workspace, f),
                    manifest_kind=_file_kind(f.name),  # type: ignore[arg-type]
                    read_target=str(f),
                    requested_view=None,
                )
            )
        except Exception:
            continue
    if registered:
        state.save()
    return registered


def reconcile_views(state: AppState) -> list[str]:
    """Rebuild every manifest view on startup. Returns ids that failed to rebuild."""
    failed: list[str] = []
    for src in state.manifest.sources:
        try:
            _create_view(state, src.view_name, src.kind, _read_target(state, src))
        except Exception:
            failed.append(src.id)
    return failed


def _upsert(state: AppState, source: Source) -> None:
    state.manifest.sources = [s for s in state.manifest.sources if s.path != source.path]
    state.manifest.sources.append(source)


def _register_target(
    state: AppState,
    *,
    manifest_path: str,
    manifest_kind: str,
    read_target: str,
    requested_view: str | None,
) -> Source:
    prior = next((s for s in state.manifest.sources if s.path == manifest_path), None)
    if prior and not requested_view:
        view_name, source_id = prior.view_name, prior.id
    else:
        taken = {s.view_name for s in state.manifest.sources if s.path != manifest_path}
        base = _sanitize_identifier(requested_view or Path(manifest_path).stem)
        view_name = _unique_view_name(base, taken)
        source_id = prior.id if prior else view_name
    _create_view(state, view_name, manifest_kind, read_target)
    source = Source(
        id=source_id,
        path=manifest_path,
        kind=manifest_kind,
        view_name=view_name,
        registered_at=_now_iso(),
    )
    _upsert(state, source)
    return source


@router.get("")
def list_sources(state: AppState = Depends(get_state)) -> dict:
    return {"sources": [s.model_dump() for s in state.manifest.sources]}


@router.post("")
def register_source(req: RegisterRequest, state: AppState = Depends(get_state)) -> dict:
    raw = req.path.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="path is required")

    registered: list[Source] = []

    if _is_remote(raw):
        registered.append(
            _register_target(
                state,
                manifest_path=raw,
                manifest_kind="remote",
                read_target=raw,
                requested_view=req.view_name,
            )
        )
    else:
        resolved = Path(raw).expanduser()
        if not resolved.is_absolute():
            resolved = state.workspace / resolved
        resolved = resolved.resolve()

        if not resolved.exists():
            raise HTTPException(status_code=404, detail=f"path not found: {raw}")

        if resolved.is_dir():
            files = _walk_supported(resolved)
            if not files:
                raise HTTPException(status_code=400, detail=f"no supported data files under {raw}")
        else:
            if not _file_kind(resolved.name):
                raise HTTPException(status_code=400, detail=f"unsupported file type: {resolved.name}")
            files = [resolved]

        multi = len(files) > 1
        for f in files:
            manifest_path = _manifest_path_for(state.workspace, f)
            registered.append(
                _register_target(
                    state,
                    manifest_path=manifest_path,
                    manifest_kind=_file_kind(f.name),  # type: ignore[arg-type]
                    read_target=str(f),
                    # a per-file view name only makes sense for a single file
                    requested_view=None if multi else req.view_name,
                )
            )

    state.save()
    return {"registered": [s.model_dump() for s in registered]}


@router.get("/{source_id}/profile")
def profile_source(source_id: str, state: AppState = Depends(get_state)) -> dict:
    source = next((s for s in state.manifest.sources if s.id == source_id), None)
    if source is None:
        raise HTTPException(status_code=404, detail=f"no such source: {source_id}")
    try:
        return profile_view(state, source.view_name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"profile error: {exc}") from exc


@router.delete("/{source_id}")
def delete_source(source_id: str, state: AppState = Depends(get_state)) -> dict:
    source = next((s for s in state.manifest.sources if s.id == source_id), None)
    if source is None:
        raise HTTPException(status_code=404, detail=f"no such source: {source_id}")
    with state.db_lock:
        state.db.execute(f"DROP VIEW IF EXISTS {_quote_ident(source.view_name)}")
    state.manifest.sources = [s for s in state.manifest.sources if s.id != source_id]
    state.save()
    return {"deleted": source_id}


def _manifest_path_for(workspace: Path, file: Path) -> str:
    try:
        return file.resolve().relative_to(workspace).as_posix()
    except ValueError:
        return str(file.resolve())
