"""MCP server exposing a running smolduck session to external agents.

Transport: stdio. The server is a thin, host-side client of the smolduck backend
(reached on 127.0.0.1 via the workspace's session file, or an explicit --url). It
runs no untrusted code itself — `run_python`/ML execute inside smolduck's microVM,
so the sandbox guarantee is inherited. A session must be live:  smolduck run <ws>.

Tool surface (analyze + create artifacts; no deletes):
  read/analyze  : smolduck_status, list_sources, get_schema, query_sql,
                  profile_source, run_python
  create        : register_source, save_notebook, create_chart,
                  run_ml_experiment, export_report, export_data
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

from .client import SmolduckClient, SmolduckError, discover_base_url

mcp = FastMCP("smolduck")

# Resolved in main(); the client is built lazily so `tools/list` works even when no
# session is running (tool calls then return a friendly error instead of crashing).
_cfg: dict[str, str | None] = {"workspace": None, "url": None}
_client: SmolduckClient | None = None


def _client_or_raise() -> SmolduckClient:
    global _client
    if _client is None:
        base = discover_base_url(_cfg["workspace"], _cfg["url"])  # raises SmolduckError
        _client = SmolduckClient(base)
    return _client


def _guard(fn: Callable[[SmolduckClient], Any]) -> Any:
    """Run a tool body against the client; turn backend failures into a clean error
    payload the calling agent can read (rather than an opaque transport exception)."""
    try:
        return fn(_client_or_raise())
    except SmolduckError as exc:
        return {"error": str(exc)}


def _out_path(out_path: str | None, default_name: str) -> Path:
    p = Path(out_path) if out_path else Path(_cfg["workspace"] or ".") / default_name
    return p.expanduser().resolve()


# ----------------------------------------------------------------- read/analyze

@mcp.tool()
def smolduck_status() -> dict:
    """Check the connected smolduck session: health, and whether the sandboxed
    Python kernel is available (run_python / ML need a microVM session)."""
    def go(c: SmolduckClient) -> dict:
        return {"base_url": c.base_url, "health": c.health(), "kernel": c.kernel_status()}
    return _guard(go)


@mcp.tool()
def list_sources() -> dict:
    """List the registered data sources (DuckDB views) in the workspace."""
    return _guard(lambda c: c.list_sources())


@mcp.tool()
def get_schema(view: str) -> dict:
    """Get the columns and types of one view (runs DESCRIBE)."""
    safe = view.replace('"', '""')
    return _guard(lambda c: c.query(f'DESCRIBE "{safe}"'))


@mcp.tool()
def query_sql(sql: str, limit: int | None = None) -> dict:
    """Run a read-only SQL query against the workspace's DuckDB and return the
    columns and rows (preview-capped). Use to explore and verify before proposing
    analysis."""
    return _guard(lambda c: c.query(sql, limit=limit))


@mcp.tool()
def profile_source(source_id: str) -> dict:
    """Statistical EDA profile for a source: per-column type, null %, distinct
    count, numeric summaries, and a correlation matrix."""
    return _guard(lambda c: c.profile_source(source_id))


@mcp.tool()
def run_python(code: str, timeout: float | None = None) -> dict:
    """Run Python in smolduck's sandboxed kernel (inside the microVM): `pd`, `pl`,
    `np`, `px`, and `sql()` are pre-imported. Returns stdout/stderr, the repr of the
    last expression, any Plotly figures, a preview DataFrame, and error/timeout info.
    Only enabled when connected to a microVM session."""
    return _guard(lambda c: c.kernel_exec(code, timeout=timeout))


# --------------------------------------------------------------- create artifacts

@mcp.tool()
def register_source(path: str, view_name: str | None = None) -> dict:
    """Register a data file/dir (or remote http/s3/gs/hf URL) as a DuckDB view."""
    return _guard(lambda c: c.register_source(path, view_name=view_name))


@mcp.tool()
def save_notebook(cells: list[dict], title: str | None = None) -> dict:
    """Persist an analysis as a notebook (opens in the human workbench). Each cell is
    {"kind": "sql"|"python"|"markdown"|"chart", "source": str, "config": {...}?}."""
    return _guard(lambda c: c.create_notebook(title, cells))


@mcp.tool()
def create_chart(query: str, config: dict, spec: dict, title: str | None = None) -> dict:
    """Pin a chart artifact: its originating SQL (`query`), the encoding (`config`,
    e.g. {type, x, y, color}), and the full Plotly `spec` ({data, layout}) that you
    construct. Saved as a portable artifact, viewable in the workbench."""
    return _guard(lambda c: c.create_chart(query, config, spec, title=title))


@mcp.tool()
def run_ml_experiment(
    source_id: str,
    features: list[str],
    target: str | None = None,
    task: str = "auto",
    test_size: float | None = None,
    k: int | None = None,
) -> dict:
    """Fit baseline models on a source (runs in the sandboxed kernel). `task` is
    auto|classification|regression|clustering. Returns models scored against a dummy
    baseline, feature importance, and metrics; each run is logged to experiments.jsonl."""
    return _guard(
        lambda c: c.ml_experiment(source_id, features, target=target, task=task, test_size=test_size, k=k)
    )


@mcp.tool()
def export_report(notebook_id: str, out_path: str | None = None) -> dict:
    """Export a notebook to a self-contained HTML report (opens offline). Writes the
    file and returns its absolute path."""
    def go(c: SmolduckClient) -> dict:
        html = c.export_notebook(notebook_id)
        dest = _out_path(out_path, f"smolduck-{notebook_id}.html")
        dest.write_bytes(html)
        return {"path": str(dest), "bytes": len(html)}
    return _guard(go)


@mcp.tool()
def export_data(sql: str, format: str = "csv", out_path: str | None = None) -> dict:
    """Export a SQL query's full result to a file (`format`: csv|parquet). Writes the
    file and returns its absolute path."""
    def go(c: SmolduckClient) -> dict:
        blob = c.export_data(sql, fmt=format)
        ext = "parquet" if format == "parquet" else "csv"
        dest = _out_path(out_path, f"smolduck-export.{ext}")
        dest.write_bytes(blob)
        return {"path": str(dest), "bytes": len(blob)}
    return _guard(go)


# ------------------------------------------------------------------------- main

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="smolduck-mcp",
        description="MCP server for a running smolduck session.",
    )
    parser.add_argument(
        "--workspace",
        default=os.environ.get("SMOLDUCK_WORKSPACE", "."),
        help="workspace folder whose .smolduck/session.json locates the backend (default: cwd).",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("SMOLDUCK_MCP_URL"),
        help="explicit backend base URL (e.g. http://127.0.0.1:8000); overrides session discovery.",
    )
    args = parser.parse_args()
    _cfg["workspace"] = args.workspace
    _cfg["url"] = args.url
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
