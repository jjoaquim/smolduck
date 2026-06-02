"""One-click dataset profile (EDA).

`SUMMARIZE` gives per-column type, min/max, mean/std/quartiles, null %, and an
*approximate* distinct count in a single pass. A secondary pass adds what
SUMMARIZE can't: exact distinct counts, an equi-width histogram for numeric
columns, top-k values for categoricals, and a pairwise correlation matrix over
numeric columns.

Read-only; all queries run under the shared `db_lock`.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any

NUMERIC_RE = re.compile(
    r"\b(TINYINT|SMALLINT|INTEGER|BIGINT|HUGEINT|UTINYINT|USMALLINT|UINTEGER|UBIGINT|DECIMAL|NUMERIC|REAL|FLOAT|DOUBLE)\b",
    re.IGNORECASE,
)
TEMPORAL_RE = re.compile(r"\b(DATE|TIME|TIMESTAMP|INTERVAL)\b", re.IGNORECASE)

HISTOGRAM_BINS = 20
TOP_K = 10


def _kind(col_type: str) -> str:
    if NUMERIC_RE.search(col_type):
        return "numeric"
    if TEMPORAL_RE.search(col_type):
        return "temporal"
    if re.search(r"BOOLEAN", col_type, re.IGNORECASE):
        return "boolean"
    return "text"


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (_dt.date, _dt.datetime, _dt.time)):
        return v.isoformat()
    return str(v)


def _histogram(con, qview: str, name: str, lo: float, hi: float) -> dict:
    qcol = _quote(name)
    if hi <= lo:  # constant column → single bin
        n = con.execute(f"SELECT count(*) FROM {qview} WHERE {qcol} IS NOT NULL").fetchone()[0]
        return {"bins": [{"lo": lo, "hi": hi, "count": int(n)}]}
    width = (hi - lo) / HISTOGRAM_BINS
    # Equi-width bucket 0..n-1 (DuckDB has no width_bucket); clamp so col==hi lands in the last.
    rows = con.execute(
        f"SELECT least(greatest(CAST(floor(({qcol} - {lo!r}) / {width!r}) AS INTEGER), 0), {HISTOGRAM_BINS - 1}) AS b, "
        f"count(*) AS c FROM {qview} WHERE {qcol} IS NOT NULL GROUP BY b"
    ).fetchall()
    counts = {int(b): int(c) for b, c in rows if b is not None}
    bins = [
        {"lo": lo + k * width, "hi": lo + (k + 1) * width, "count": counts.get(k, 0)}
        for k in range(HISTOGRAM_BINS)
    ]
    return {"bins": bins}


def _top_k(con, qview: str, name: str) -> list[dict]:
    rows = con.execute(
        f"SELECT {_quote(name)} AS v, count(*) AS c FROM {qview} GROUP BY 1 ORDER BY c DESC, 1 LIMIT {TOP_K}"
    ).fetchall()
    return [{"value": _jsonable(v), "count": int(c)} for v, c in rows]


def _correlation(con, qview: str, names: list[str]) -> dict:
    selects, pairs = [], []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            selects.append(f"corr({_quote(names[i])}, {_quote(names[j])})")
            pairs.append((i, j))
    row = con.execute(f"SELECT {', '.join(selects)} FROM {qview}").fetchone()
    n = len(names)
    matrix = [[1.0 if i == j else None for j in range(n)] for i in range(n)]
    for (i, j), val in zip(pairs, row):
        v = None if val is None else round(float(val), 4)
        matrix[i][j] = matrix[j][i] = v
    return {"columns": names, "matrix": matrix}


def profile_view(state, view_name: str) -> dict:
    """Profile one DuckDB view/table. Holds db_lock for the whole pass."""
    qview = _quote(view_name)
    with state.db_lock:
        row_count = int(state.db.execute(f"SELECT count(*) FROM {qview}").fetchone()[0])

        summ = state.db.sql(f"SUMMARIZE {qview}")
        scols = summ.columns
        columns: list[dict] = []
        for raw in summ.fetchall():
            d = dict(zip(scols, raw))
            columns.append(
                {
                    "name": d["column_name"],
                    "type": d["column_type"],
                    "kind": _kind(d["column_type"]),
                    "null_pct": round(float(d["null_percentage"]), 2) if d["null_percentage"] is not None else 0.0,
                    "approx_distinct": int(d["approx_unique"]) if d["approx_unique"] is not None else None,
                    "min": _jsonable(d["min"]),
                    "max": _jsonable(d["max"]),
                    "avg": _num(d["avg"]),
                    "std": _num(d["std"]),
                    "q25": _num(d["q25"]),
                    "q50": _num(d["q50"]),
                    "q75": _num(d["q75"]),
                    "histogram": None,
                    "top_k": None,
                }
            )

        names = [c["name"] for c in columns]
        if names:
            sel = ", ".join(f"count(DISTINCT {_quote(n)})" for n in names)
            drow = state.db.execute(f"SELECT {sel} FROM {qview}").fetchone()
            for c, dv in zip(columns, drow):
                c["distinct"] = int(dv)

        for c in columns:
            if c["kind"] == "numeric" and c["distinct"] > 0:
                lo, hi = _num(c["min"]), _num(c["max"])
                if lo is not None and hi is not None:
                    c["histogram"] = _histogram(state.db, qview, c["name"], lo, hi)
            else:
                c["top_k"] = _top_k(state.db, qview, c["name"])

        numeric = [c["name"] for c in columns if c["kind"] == "numeric" and c["distinct"] > 1]
        correlation = _correlation(state.db, qview, numeric) if len(numeric) >= 2 else None

    return {"view_name": view_name, "row_count": row_count, "columns": columns, "correlation": correlation}
