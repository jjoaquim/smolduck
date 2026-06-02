"""Workspace resolution and the `.smolduck/manifest.json` document.

smolduck stores only metadata and artifacts on disk; the user's data lives in
DuckDB. The manifest is the portable, git-friendly record of what a workspace
contains so everything reconstructs on relaunch.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

SCHEMA_VERSION = "0.1.0"
SMOLDUCK_DIRNAME = ".smolduck"
MANIFEST_FILENAME = "manifest.json"
WORKSPACE_ENV = "SMOLDUCK_WORKSPACE"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Source(BaseModel):
    id: str
    path: str
    kind: str  # csv | parquet | json | xlsx | remote
    view_name: str
    registered_at: str = Field(default_factory=_now_iso)


class Settings(BaseModel):
    preview_row_cap: int = 1000
    default_chart_lib: str = "plotly"
    agent_enabled: bool = False


class Manifest(BaseModel):
    version: str = SCHEMA_VERSION
    created_at: str = Field(default_factory=_now_iso)
    sources: list[Source] = Field(default_factory=list)
    settings: Settings = Field(default_factory=Settings)


def resolve_workspace_dir(path: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the workspace folder: explicit arg, then $SMOLDUCK_WORKSPACE, then CWD."""
    if path is None:
        path = os.environ.get(WORKSPACE_ENV) or os.getcwd()
    return Path(path).expanduser().resolve()


def smolduck_dir(workspace: Path) -> Path:
    return workspace / SMOLDUCK_DIRNAME


def ensure_smolduck_dir(workspace: Path) -> Path:
    d = smolduck_dir(workspace)
    d.mkdir(parents=True, exist_ok=True)
    return d


def manifest_path(workspace: Path) -> Path:
    return smolduck_dir(workspace) / MANIFEST_FILENAME


def load_manifest(workspace: Path) -> Manifest:
    """Load the manifest, creating a default one on disk if absent."""
    path = manifest_path(workspace)
    if path.exists():
        return Manifest.model_validate_json(path.read_text())
    manifest = Manifest()
    save_manifest(workspace, manifest)
    return manifest


def save_manifest(workspace: Path, manifest: Manifest) -> None:
    ensure_smolduck_dir(workspace)
    manifest_path(workspace).write_text(manifest.model_dump_json(indent=2))
