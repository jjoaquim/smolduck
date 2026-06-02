"""SQL query path: paginated preview (`POST /api/query`), Arrow streaming for
large results (`/ws/query`), and full-result Parquet export.

DuckDB pushes the preview LIMIT into the plan, so a `SELECT *` over a huge table
only materializes one page. The WebSocket path streams record batches from a
DuckDB reader, so large results never need to fit in memory at once.
"""

from __future__ import annotations

import os
import tempfile
import time

import pyarrow as pa
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from .state import AppState, get_state

router = APIRouter(tags=["query"])

DEFAULT_STREAM_BATCH_ROWS = 50_000


class QueryRequest(BaseModel):
    sql: str
    limit: int | None = None
    offset: int = 0


class ExportRequest(BaseModel):
    sql: str


def _jsonable(value):
    # FastAPI's encoder already handles date/datetime/Decimal; binary needs help.
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return value


def _strip_trailing_semicolons(sql: str) -> str:
    return sql.strip().rstrip(";").strip()


@router.post("/api/query")
def run_query(req: QueryRequest, state: AppState = Depends(get_state)) -> dict:
    sql = req.sql.strip()
    if not sql:
        raise HTTPException(status_code=400, detail="sql is required")

    cap = req.limit if req.limit is not None else state.manifest.settings.preview_row_cap
    cap = max(0, cap)
    offset = max(0, req.offset)

    start = time.perf_counter()
    # Hold the lock across plan + fetch: the relation is lazy, so the work
    # actually happens in fetchall(), and a second concurrent query on the shared
    # connection would corrupt it mid-flight.
    with state.db_lock:
        try:
            rel = state.db.sql(sql)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"query error: {exc}") from exc

        if rel is None:
            # Non-result statement (DDL / SET / PRAGMA / COPY ...).
            return {
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "offset": offset,
                "limit": cap,
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
                "statement": True,
            }

        columns = [{"name": n, "type": str(t)} for n, t in zip(rel.columns, rel.types)]
        try:
            fetched = rel.limit(cap + 1, offset=offset).fetchall()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"query error: {exc}") from exc

    truncated = len(fetched) > cap
    rows = [[_jsonable(v) for v in row] for row in fetched[:cap]]
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "offset": offset,
        "limit": cap,
        "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
    }


@router.post("/api/query/export")
def export_query(req: ExportRequest, state: AppState = Depends(get_state)) -> FileResponse:
    sql = _strip_trailing_semicolons(req.sql)
    if not sql:
        raise HTTPException(status_code=400, detail="sql is required")

    fd, tmp = tempfile.mkstemp(suffix=".parquet", prefix="smolduck_export_")
    os.close(fd)
    escaped = tmp.replace("'", "''")
    try:
        with state.db_lock:
            state.db.execute(f"COPY ({sql}) TO '{escaped}' (FORMAT parquet)")
    except Exception as exc:
        os.remove(tmp)
        raise HTTPException(status_code=400, detail=f"export error: {exc}") from exc

    return FileResponse(
        tmp,
        media_type="application/vnd.apache.parquet",
        filename="result.parquet",
        background=BackgroundTask(os.remove, tmp),
    )


def _frame_bytes(batch: pa.RecordBatch) -> bytes:
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, batch.schema) as writer:
        writer.write_batch(batch)
    return sink.getvalue().to_pybytes()


@router.websocket("/ws/query")
async def ws_query(ws: WebSocket) -> None:
    await ws.accept()
    state: AppState = ws.app.state.smol

    try:
        req = await ws.receive_json()
    except Exception:
        await ws.close()
        return

    sql = (req.get("sql") or "").strip() if isinstance(req, dict) else ""
    if not sql:
        await ws.send_json({"type": "error", "error": "sql is required"})
        await ws.close()
        return
    batch_rows = int(req.get("batch_size") or DEFAULT_STREAM_BATCH_ROWS)

    start = time.perf_counter()
    try:
        with state.db_lock:
            rel = state.db.sql(sql)
            if rel is None:
                await ws.send_json({"type": "end", "row_count": 0, "batches": 0, "elapsed_ms": 0, "statement": True})
                await ws.close()
                return
            reader = rel.to_arrow_reader(batch_rows)
    except Exception as exc:
        await ws.send_json({"type": "error", "error": str(exc)})
        await ws.close()
        return

    await ws.send_json(
        {"type": "schema", "columns": [{"name": f.name, "type": str(f.type)} for f in reader.schema]}
    )

    total = 0
    batches = 0
    try:
        while True:
            # Pull each batch under the lock (the reader shares the one
            # connection), but release it while we await the network send.
            with state.db_lock:
                try:
                    batch = next(reader)
                except StopIteration:
                    break
            if batch.num_rows == 0:
                continue
            await ws.send_bytes(_frame_bytes(batch))
            total += batch.num_rows
            batches += 1
        await ws.send_json(
            {
                "type": "end",
                "row_count": total,
                "batches": batches,
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
            }
        )
    except WebSocketDisconnect:
        return
    finally:
        try:
            await ws.close()
        except RuntimeError:
            pass
