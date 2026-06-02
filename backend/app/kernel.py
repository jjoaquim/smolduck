"""A persistent Python kernel that runs cell code — only ever inside the VM.

The kernel is a separate **spawned process** (not a thread) so a runaway cell can
be killed and the kernel restarted cleanly. It does not open the
DuckDB store itself — that file is held read-write by the backend process — so a
cell's `sql()` helper proxies the query back to the parent, which runs it on the
shared connection and ships the result as Arrow IPC. That is how the kernel
"shares the connection" across the process boundary.

Wire protocol over a `multiprocessing` duplex Pipe (pickled objects):
  parent → child : {"op": "exec", "code"} | {"op": "sql_response", ...} | {"op": "shutdown"}
  child  → parent: {"t": "stdout"|"stderr"|"sql_request"|"figure"|"dataframe"|"result"|"error"|"done", ...}

Gating lives in `sandbox.py`; this module assumes the caller already checked
`kernel_enabled()`.
"""

from __future__ import annotations

import ast
import io
import multiprocessing as mp
import sys
import threading
import time
import traceback
from typing import Any, Callable

DEFAULT_TIMEOUT_S = 120.0
DF_PREVIEW_ROWS = 50


# ----------------------------------------------------------------- worker side

def _json_safe(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if v == v and v not in (float("inf"), float("-inf")) else None  # NaN/inf → null
    # numpy scalars
    item = getattr(v, "item", None)
    if callable(item):
        try:
            return _json_safe(item())
        except Exception:
            pass
    return str(v)


def _df_preview(value: Any) -> dict | None:
    """A capped, JSON-safe preview of a pandas/polars DataFrame, else None."""
    cls = type(value)
    mod = cls.__module__.split(".")[0]
    if mod == "pandas" and cls.__name__ == "DataFrame":
        head = value.head(DF_PREVIEW_ROWS)
        cols = [str(c) for c in value.columns]
        rows = [[_json_safe(v) for v in row] for row in head.itertuples(index=False, name=None)]
        return {"columns": cols, "rows": rows, "shape": [int(value.shape[0]), int(value.shape[1])],
                "truncated": int(value.shape[0]) > DF_PREVIEW_ROWS}
    if mod == "polars" and cls.__name__ == "DataFrame":
        head = value.head(DF_PREVIEW_ROWS)
        cols = list(value.columns)
        rows = [[_json_safe(v) for v in row] for row in head.iter_rows()]
        return {"columns": cols, "rows": rows, "shape": [int(value.shape[0]), int(value.shape[1])],
                "truncated": int(value.shape[0]) > DF_PREVIEW_ROWS}
    return None


def _is_plotly_figure(value: Any) -> bool:
    return type(value).__module__.startswith("plotly.graph_objs") and hasattr(value, "to_plotly_json")


def _exec_capture(code: str, ns: dict) -> Any:
    """Exec the cell; if it ends in an expression, return that value (REPL-style)."""
    tree = ast.parse(code, mode="exec")
    last_expr = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last_expr = ast.Expression(tree.body.pop().value)
    if tree.body:
        exec(compile(tree, "<cell>", "exec"), ns)
    if last_expr is not None:
        return eval(compile(last_expr, "<cell>", "eval"), ns)
    return None


class _StreamWriter(io.TextIOBase):
    def __init__(self, conn, kind: str) -> None:
        self._conn = conn
        self._kind = kind

    def write(self, s: str) -> int:
        if s:
            self._conn.send({"t": self._kind, "text": s})
        return len(s)


def _worker_main(conn, store_path: str) -> None:  # pragma: no cover - runs in child process
    import json

    import pandas as pd
    import polars as pl
    import numpy as np
    import plotly.express as px
    import plotly.graph_objects as go
    import plotly.io as pio
    import pyarrow as pa

    def sql(query: str):
        """Run SQL on the workspace's DuckDB (proxied to the host process)."""
        conn.send({"t": "sql_request", "sql": query})
        resp = conn.recv()
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error") or "sql failed")
        table = pa.ipc.open_stream(resp["arrow"]).read_all()
        return table.to_pandas()

    ns: dict = {
        "__name__": "__smolduck_cell__",
        "pd": pd, "pl": pl, "np": np, "px": px, "go": go,
        "sql": sql,
    }

    while True:
        try:
            msg = conn.recv()
        except (EOFError, KeyboardInterrupt):
            break
        if not isinstance(msg, dict) or msg.get("op") == "shutdown":
            break

        if msg.get("op") == "ml":
            try:
                from . import ml_core
                result = ml_core.run_experiment(msg["spec"], sql)
                conn.send({"t": "ml_result", "result": result})
            except Exception as exc:
                conn.send({"t": "error", "error": f"{type(exc).__name__}: {exc}",
                           "traceback": traceback.format_exc()})
            finally:
                conn.send({"t": "done"})
            continue

        if msg.get("op") != "exec":
            continue

        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = _StreamWriter(conn, "stdout"), _StreamWriter(conn, "stderr")
        try:
            value = _exec_capture(msg.get("code", ""), ns)
            if _is_plotly_figure(value):
                conn.send({"t": "figure", "figure": json.loads(pio.to_json(value))})
            else:
                preview = _df_preview(value)
                if preview is not None:
                    conn.send({"t": "dataframe", **preview})
                elif value is not None:
                    conn.send({"t": "result", "repr": repr(value)[:4000]})
        except Exception as exc:
            conn.send({"t": "error", "error": f"{type(exc).__name__}: {exc}",
                       "traceback": traceback.format_exc()})
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            conn.send({"t": "done"})


# ----------------------------------------------------------------- parent side

class KernelManager:
    """Owns the kernel subprocess and serializes execution (the kernel is stateful)."""

    def __init__(self, state) -> None:
        self.state = state
        self._proc: mp.process.BaseProcess | None = None
        self._conn = None
        self._lock = threading.Lock()  # one cell at a time

    # -- lifecycle --
    def _spawn(self) -> None:
        ctx = mp.get_context("spawn")
        parent_conn, child_conn = ctx.Pipe()
        proc = ctx.Process(
            target=_worker_main,
            args=(child_conn, str(self.state.workspace)),
            daemon=True,
            name="smolduck-kernel",
        )
        proc.start()
        child_conn.close()  # parent keeps only its end
        self._proc, self._conn = proc, parent_conn

    def _ensure(self) -> None:
        if self._proc is None or not self._proc.is_alive():
            self._kill()
            self._spawn()

    def _kill(self) -> None:
        if self._proc is not None and self._proc.is_alive():
            self._proc.kill()
            self._proc.join(timeout=5)
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._proc, self._conn = None, None

    def restart(self) -> None:
        with self._lock:
            self._kill()
            self._spawn()

    def shutdown(self) -> None:
        with self._lock:
            self._kill()

    # -- sql proxy --
    def _answer_sql(self, msg: dict) -> None:
        import pyarrow as pa

        try:
            with self.state.db_lock:
                rel = self.state.db.sql(msg["sql"])
                table = rel.to_arrow_table() if rel is not None else pa.table({})
            sink = pa.BufferOutputStream()
            with pa.ipc.new_stream(sink, table.schema) as w:
                w.write_table(table)
            self._conn.send({"op": "sql_response", "ok": True, "arrow": sink.getvalue().to_pybytes()})
        except Exception as exc:
            self._conn.send({"op": "sql_response", "ok": False, "error": str(exc)})

    # -- execution --
    def run(self, code: str, emit: Callable[[dict], None], timeout: float = DEFAULT_TIMEOUT_S) -> None:
        """Execute `code`, streaming events to `emit`. See `_drive` for timeout semantics."""
        self._drive({"op": "exec", "code": code}, emit, timeout)

    def _drive(self, msg: dict, emit: Callable[[dict], None], timeout: float) -> None:
        """Send one op to the worker and pump its events to `emit` until `done`.
        Enforces a wall-clock timeout: on overrun the kernel is killed, a
        `{"t": "timeout"}` event is emitted, and a fresh kernel is spawned."""
        with self._lock:
            self._ensure()
            try:
                self._conn.send(msg)
            except Exception as exc:
                emit({"t": "error", "error": f"kernel send failed: {exc}", "traceback": ""})
                self._kill()
                return

            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._kill()
                    emit({"t": "timeout", "seconds": timeout})
                    emit({"t": "done"})
                    self._spawn()  # fresh kernel for the next cell
                    return
                if not self._conn.poll(remaining):
                    continue
                try:
                    msg = self._conn.recv()
                except (EOFError, OSError):
                    self._kill()
                    emit({"t": "error", "error": "kernel died unexpectedly", "traceback": ""})
                    emit({"t": "done"})
                    self._spawn()
                    return
                if msg.get("t") == "sql_request":
                    self._answer_sql(msg)
                    continue
                emit(msg)
                if msg.get("t") == "done":
                    return

    def run_collect(self, code: str, timeout: float = DEFAULT_TIMEOUT_S) -> dict:
        """Run and aggregate events into a single structured result (for the POST API)."""
        out: dict = {
            "stdout": "", "stderr": "", "figures": [], "dataframe": None,
            "result": None, "error": None, "timed_out": False,
        }
        start = time.perf_counter()

        def emit(ev: dict) -> None:
            t = ev.get("t")
            if t == "stdout":
                out["stdout"] += ev["text"]
            elif t == "stderr":
                out["stderr"] += ev["text"]
            elif t == "figure":
                out["figures"].append(ev["figure"])
            elif t == "dataframe":
                out["dataframe"] = {k: ev[k] for k in ("columns", "rows", "shape", "truncated")}
            elif t == "result":
                out["result"] = ev["repr"]
            elif t == "error":
                out["error"] = ev.get("error")
                out["traceback"] = ev.get("traceback")
            elif t == "timeout":
                out["timed_out"] = True
                out["error"] = f"cell exceeded {ev['seconds']:.0f}s timeout — kernel restarted"

        self.run(code, emit, timeout=timeout)
        out["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 3)
        return out

    def run_ml(self, spec: dict, timeout: float = 300.0) -> dict:
        """Run a baseline ML experiment in the kernel; returns the structured result."""
        out: dict = {"result": None, "error": None, "traceback": None, "timed_out": False, "stdout": ""}
        start = time.perf_counter()

        def emit(ev: dict) -> None:
            t = ev.get("t")
            if t == "ml_result":
                out["result"] = ev["result"]
            elif t == "stdout":
                out["stdout"] += ev.get("text", "")
            elif t == "error":
                out["error"] = ev.get("error")
                out["traceback"] = ev.get("traceback")
            elif t == "timeout":
                out["timed_out"] = True
                out["error"] = f"experiment exceeded {ev['seconds']:.0f}s timeout — kernel restarted"

        self._drive({"op": "ml", "spec": spec}, emit, timeout)
        out["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 3)
        return out


def get_kernel(state) -> KernelManager:
    """Lazily create and cache the per-workspace kernel on AppState."""
    km = getattr(state, "kernel", None)
    if km is None:
        km = KernelManager(state)
        state.kernel = km
    return km
