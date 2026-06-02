"""Export: a notebook → a self-contained HTML report, and query results → CSV/Parquet.

The HTML report embeds plotly.js inline (via `get_plotlyjs`) so it opens
standalone with no network — markdown is rendered, SQL/DataFrame results become
tables, and python/chart figures render from their stored/derived Plotly specs.
(Chart images are exported client-side via Plotly's modebar; data files come
straight from DuckDB `COPY`.)
"""

from __future__ import annotations

import html as _html
import os
import re
import tempfile

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from starlette.background import BackgroundTask

from . import notebooks
from .chart_build import build_figure
from .state import AppState, get_state

router = APIRouter(prefix="/api/export", tags=["export"])


class DataExportRequest(BaseModel):
    sql: str
    format: str = "csv"  # csv | parquet


# --------------------------------------------------------------- HTML render

def _esc(s) -> str:
    return _html.escape("" if s is None else str(s))


def _render_markdown(src: str) -> str:
    """Minimal markdown → HTML (headings, bold/italic/code, lists, paragraphs)."""
    out, lst = [], None
    for line in (src or "").replace("\r\n", "\n").split("\n"):
        h = re.match(r"^(#{1,6})\s+(.*)$", line)
        ul = re.match(r"^\s*[-*+]\s+(.*)$", line)
        if h:
            if lst:
                out.append(f"</{lst}>"); lst = None
            out.append(f"<h{len(h.group(1))}>{_inline(h.group(2))}</h{len(h.group(1))}>")
        elif ul:
            if lst != "ul":
                out.append("<ul>"); lst = "ul"
            out.append(f"<li>{_inline(ul.group(1))}</li>")
        elif line.strip() == "":
            if lst:
                out.append(f"</{lst}>"); lst = None
        else:
            if lst:
                out.append(f"</{lst}>"); lst = None
            out.append(f"<p>{_inline(line)}</p>")
    if lst:
        out.append(f"</{lst}>")
    return "\n".join(out)


def _inline(t: str) -> str:
    t = _esc(t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", t)
    return t


def _normalize_columns(columns) -> list[str]:
    if not columns:
        return []
    if isinstance(columns[0], dict):
        return [str(c.get("name", "")) for c in columns]
    return [str(c) for c in columns]


def _table(columns, rows) -> str:
    names = _normalize_columns(columns)
    if not names:
        return ""
    head = "".join(f"<th>{_esc(n)}</th>" for n in names)
    body = "".join(
        "<tr>" + "".join(f"<td>{_esc(v)}</td>" for v in row) + "</tr>" for row in (rows or [])[:200]
    )
    return f'<table class="grid"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def _figure_html(spec: dict) -> str:
    import plotly.graph_objects as go
    import plotly.io as pio

    if not spec or not spec.get("data"):
        return ""
    fig = go.Figure(data=spec.get("data", []), layout=spec.get("layout", {}))
    return pio.to_html(fig, include_plotlyjs=False, full_html=False,
                       default_height="420px", config={"displaylogo": False})


def _render_cell(cell, figures: list) -> str:
    kind, src, lr, cfg = cell.kind, cell.source, cell.last_result, (cell.config or {})

    if kind == "markdown":
        return f'<section class="cell md">{_render_markdown(src)}</section>'

    parts = [f'<div class="kind">{kind.upper()}</div>']
    if kind != "chart":
        parts.append(f"<pre class=\"src\">{_esc(src)}</pre>")

    if kind == "sql" and isinstance(lr, dict) and lr.get("columns") is not None:
        parts.append(_table(lr["columns"], lr.get("rows")))
    elif kind == "python" and isinstance(lr, dict):
        if lr.get("stdout"):
            parts.append(f'<pre class="out">{_esc(lr["stdout"])}</pre>')
        for fig in lr.get("figures") or []:
            figures.append(fig)
            parts.append(_figure_html(fig))
        if lr.get("dataframe"):
            df = lr["dataframe"]
            parts.append(_table(df.get("columns"), df.get("rows")))
        if lr.get("result") is not None:
            parts.append(f'<pre class="out">{_esc(lr["result"])}</pre>')
    elif kind == "chart" and isinstance(lr, dict) and lr.get("columns") is not None:
        spec = build_figure(lr["columns"], lr.get("rows") or [], cfg)
        if spec:
            figures.append(spec)
            parts.append(_figure_html(spec))
        else:
            parts.append('<p class="muted">chart not configured</p>')

    return f'<section class="cell">{"".join(parts)}</section>'


_REPORT_CSS = """
:root { color-scheme: light; }
body { font-family: -apple-system, system-ui, sans-serif; max-width: 920px; margin: 0 auto; padding: 32px 24px 80px; color: #1a1a1a; }
h1.report-title { font-size: 26px; margin-bottom: 4px; }
.report-meta { color: #777; font-size: 13px; margin-bottom: 24px; }
section.cell { margin: 18px 0; }
.kind { font: 700 11px ui-monospace, monospace; letter-spacing: .5px; color: #b5852a; margin-bottom: 4px; }
pre.src { background: #f6f4ef; border: 1px solid #e6e0d6; border-radius: 8px; padding: 10px 12px; overflow:auto; font: 12.5px/1.5 ui-monospace, monospace; }
pre.out { background: #1d1812; color: #f3ece0; border-radius: 8px; padding: 10px 12px; overflow:auto; font: 12.5px/1.5 ui-monospace, monospace; }
table.grid { border-collapse: collapse; font: 12.5px ui-monospace, monospace; margin: 8px 0; width: 100%; }
table.grid th, table.grid td { border: 1px solid #e6e0d6; padding: 4px 8px; text-align: left; }
table.grid th { background: #f6f4ef; }
.md h1,.md h2,.md h3 { margin: 12px 0 6px; }
.muted { color: #999; }
"""


def render_notebook(nb) -> str:
    figures: list = []
    cells_html = "\n".join(_render_cell(c, figures) for c in nb.cells)
    plotlyjs = ""
    if figures:
        from plotly.offline import get_plotlyjs
        plotlyjs = f"<script>{get_plotlyjs()}</script>"
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        f"<title>{_esc(nb.title)}</title><style>{_REPORT_CSS}</style>{plotlyjs}</head><body>"
        f'<h1 class="report-title">{_esc(nb.title)}</h1>'
        f'<div class="report-meta">smolduck notebook · {_esc(nb.updated_at)} · {len(nb.cells)} cells</div>'
        f"{cells_html}</body></html>"
    )


def _safe_filename(title: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", title).strip("_") or "notebook"
    return s[:80]


# ------------------------------------------------------------------- routes

@router.get("/notebook/{notebook_id}")
def export_notebook(notebook_id: str, state: AppState = Depends(get_state)) -> Response:
    nb = notebooks._load(state, notebook_id)  # 404 if missing
    body = render_notebook(nb)
    return Response(
        content=body,
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="{_safe_filename(nb.title)}.html"'},
    )


@router.post("/data")
def export_data(req: DataExportRequest, state: AppState = Depends(get_state)) -> FileResponse:
    sql = req.sql.strip().rstrip(";").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="sql is required")
    fmt = req.format.lower()
    if fmt not in ("csv", "parquet"):
        raise HTTPException(status_code=400, detail="format must be csv or parquet")

    suffix = ".csv" if fmt == "csv" else ".parquet"
    fd, tmp = tempfile.mkstemp(suffix=suffix, prefix="smolduck_export_")
    os.close(fd)
    copy_opts = "FORMAT csv, HEADER" if fmt == "csv" else "FORMAT parquet"
    escaped = tmp.replace("'", "''")
    try:
        with state.db_lock:
            state.db.execute(f"COPY ({sql}) TO '{escaped}' ({copy_opts})")
    except Exception as exc:
        os.remove(tmp)
        raise HTTPException(status_code=400, detail=f"export error: {exc}") from exc

    media = "text/csv" if fmt == "csv" else "application/vnd.apache.parquet"
    return FileResponse(tmp, media_type=media, filename=f"result{suffix}",
                        background=BackgroundTask(os.remove, tmp))
