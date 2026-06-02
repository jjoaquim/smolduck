"""Build a Plotly figure spec from a query result + encoding config.

A Python mirror of frontend/lib/chart.js `buildFigure`, used to render chart
cells when exporting a notebook to HTML. Kept in sync with the JS.
"""

from __future__ import annotations

from typing import Any


def _col_index(columns: list[dict], name: str | None) -> int:
    if not name:
        return -1
    for i, c in enumerate(columns):
        if c.get("name") == name:
            return i
    return -1


def _col_values(columns, rows, name):
    i = _col_index(columns, name)
    return None if i < 0 else [r[i] for r in rows]


def _xy_traces(columns, rows, config, base):
    xs = _col_values(columns, rows, config.get("x"))
    ys = _col_values(columns, rows, config.get("y"))
    if xs is None or ys is None:
        return []
    color = config.get("color")
    if color:
        ci = _col_index(columns, color)
        groups: dict[Any, dict] = {}
        for ri, r in enumerate(rows):
            k = r[ci]
            groups.setdefault(k, {"x": [], "y": []})
            groups[k]["x"].append(xs[ri])
            groups[k]["y"].append(ys[ri])
        return [{**base, "name": str(k), "x": d["x"], "y": d["y"]} for k, d in groups.items()]
    return [{**base, "x": xs, "y": ys}]


def _hist_traces(columns, rows, config):
    xs = _col_values(columns, rows, config.get("x"))
    if xs is None:
        return []
    color = config.get("color")
    if color:
        ci = _col_index(columns, color)
        groups: dict[Any, list] = {}
        for ri, r in enumerate(rows):
            groups.setdefault(r[ci], []).append(xs[ri])
        return [{"type": "histogram", "name": str(k), "x": v, "opacity": 0.7} for k, v in groups.items()]
    return [{"type": "histogram", "x": xs}]


def _box_traces(columns, rows, config):
    ys = _col_values(columns, rows, config.get("y"))
    if ys is None:
        return []
    xs = _col_values(columns, rows, config.get("x")) if config.get("x") else None
    t = {"type": "box", "y": ys, "name": config.get("y")}
    if xs is not None:
        t["x"] = xs
    return [t]


def _heatmap_traces(columns, rows, config):
    xs = _col_values(columns, rows, config.get("x"))
    ys = _col_values(columns, rows, config.get("y"))
    zs = _col_values(columns, rows, config.get("z"))
    if xs is None or ys is None or zs is None:
        return []
    ux, uy = list(dict.fromkeys(xs)), list(dict.fromkeys(ys))
    z = [[None] * len(ux) for _ in uy]
    for ri in range(len(rows)):
        xi, yi = ux.index(xs[ri]), uy.index(ys[ri])
        z[yi][xi] = zs[ri]
    return [{"type": "heatmap", "x": ux, "y": uy, "z": z, "colorscale": "Viridis"}]


def build_figure(columns: list[dict], rows: list, config: dict) -> dict | None:
    if not columns or rows is None or not config or not config.get("type"):
        return None
    t = config["type"]
    if t == "bar":
        data = _xy_traces(columns, rows, config, {"type": "bar"})
    elif t == "line":
        data = _xy_traces(columns, rows, config, {"type": "scatter", "mode": "lines+markers"})
    elif t == "scatter":
        data = _xy_traces(columns, rows, config, {"type": "scatter", "mode": "markers"})
    elif t == "histogram":
        data = _hist_traces(columns, rows, config)
    elif t == "box":
        data = _box_traces(columns, rows, config)
    elif t == "heatmap":
        data = _heatmap_traces(columns, rows, config)
    else:
        data = []
    if not data:
        return None

    y_title = "count" if t == "histogram" else (config.get("y") or config.get("z") or "")
    layout = {
        "margin": {"t": 36 if config.get("title") else 20, "r": 16, "b": 44, "l": 60},
        "showlegend": bool(config.get("color")),
        "xaxis": {"title": {"text": config.get("x") or ""}},
        "yaxis": {"title": {"text": y_title}},
    }
    if config.get("title"):
        layout["title"] = {"text": config["title"]}
    if t == "bar" and config.get("color"):
        layout["barmode"] = "group"
    if t == "histogram" and config.get("color"):
        layout["barmode"] = "overlay"
    return {"data": data, "layout": layout}
