"""Baseline ML experiments — pure sklearn, run inside the kernel subprocess.

`run_experiment(spec, sql)` fetches the dataset via the kernel's `sql()` proxy,
fits a handful of baseline models (always including a trivial Dummy baseline so
metrics can be judged "sane vs a known baseline"), and returns metrics, RF
feature importance, and a confusion matrix (classification) or residuals
(regression). No FastAPI/DuckDB imports — this module is imported lazily by the
kernel worker, so heavy sklearn only loads when an experiment actually runs.
"""

from __future__ import annotations

from typing import Any, Callable


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _f(v: Any) -> float | None:
    try:
        return None if v is None else round(float(v), 6)
    except (TypeError, ValueError):
        return None


def _infer_task(y) -> str:
    import pandas as pd

    if y is None:
        return "clustering"
    if pd.api.types.is_numeric_dtype(y) and y.nunique() > 12:
        return "regression"
    return "classification"


def run_experiment(spec: dict, sql: Callable[[str], Any]) -> dict:
    import numpy as np
    import pandas as pd
    from sklearn.model_selection import train_test_split

    view = spec["view_name"]
    features = list(spec.get("features") or [])
    target = spec.get("target") or None
    task = spec.get("task") or "auto"
    test_size = float(spec.get("test_size") or 0.25)
    if not features:
        raise ValueError("at least one feature column is required")

    cols = features + ([target] if target else [])
    sel = ", ".join(_quote(c) for c in cols)
    df = sql(f"SELECT {sel} FROM {_quote(view)}").dropna(subset=cols)
    if len(df) < 5:
        raise ValueError(f"not enough complete rows to model ({len(df)})")

    y = df[target] if target else None
    if task == "auto":
        task = _infer_task(y)

    # One-hot encode categorical features (baseline-simple).
    X = pd.get_dummies(df[features], drop_first=False)
    X = X.astype(float)
    feat_names = list(X.columns)

    out: dict = {
        "view_name": view, "task": task, "target": target, "features": features,
        "n_rows": int(len(df)), "n_features": int(X.shape[1]),
        "models": [], "best_model": None, "feature_importance": None,
        "confusion_matrix": None, "residuals": None,
    }

    if task == "clustering":
        return _clustering(out, X, feat_names, spec, np)
    if task == "regression":
        return _regression(out, X, y, feat_names, test_size, np, train_test_split)
    return _classification(out, X, y, feat_names, test_size, np, pd, train_test_split)


def _importance(model, feat_names) -> list[dict]:
    imp = getattr(model, "feature_importances_", None)
    if imp is None:
        return []
    pairs = sorted(zip(feat_names, imp), key=lambda p: p[1], reverse=True)
    return [{"feature": str(f), "importance": _f(v)} for f, v in pairs]


def _classification(out, X, y, feat_names, test_size, np, pd, train_test_split):
    from sklearn.dummy import DummyClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import accuracy_score, f1_score, confusion_matrix

    y = y.astype(str)
    if y.nunique() < 2:
        raise ValueError("classification target has a single class")

    counts = y.value_counts()
    strat = y if counts.min() >= 2 else None
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=test_size, random_state=0, stratify=strat)

    rf = RandomForestClassifier(n_estimators=120, random_state=0)
    models = {
        "baseline (most frequent)": DummyClassifier(strategy="most_frequent"),
        "logistic regression": LogisticRegression(max_iter=2000),
        "random forest": rf,
    }
    for name, m in models.items():
        m.fit(Xtr, ytr)
        pred = m.predict(Xte)
        out["models"].append({
            "name": name,
            "metrics": {
                "accuracy": _f(accuracy_score(yte, pred)),
                "f1_macro": _f(f1_score(yte, pred, average="macro", zero_division=0)),
            },
        })

    out["metric_primary"] = "accuracy"
    ranked = [m for m in out["models"] if m["name"] != "baseline (most frequent)"]
    best = max(ranked, key=lambda m: m["metrics"]["accuracy"])
    out["best_model"] = best["name"]
    out["feature_importance"] = _importance(rf, feat_names)

    labels = sorted(y.unique().tolist())
    pred = rf.predict(Xte)
    cm = confusion_matrix(yte, pred, labels=labels)
    out["confusion_matrix"] = {"labels": labels, "matrix": [[int(v) for v in row] for row in cm]}
    return out


def _regression(out, X, y, feat_names, test_size, np, train_test_split):
    from sklearn.dummy import DummyRegressor
    from sklearn.linear_model import LinearRegression
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error

    y = y.astype(float)
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=test_size, random_state=0)

    rf = RandomForestRegressor(n_estimators=120, random_state=0)
    models = {
        "baseline (mean)": DummyRegressor(strategy="mean"),
        "linear regression": LinearRegression(),
        "random forest": rf,
    }
    for name, m in models.items():
        m.fit(Xtr, ytr)
        pred = m.predict(Xte)
        out["models"].append({
            "name": name,
            "metrics": {
                "r2": _f(r2_score(yte, pred)),
                "mae": _f(mean_absolute_error(yte, pred)),
                "rmse": _f(mean_squared_error(yte, pred) ** 0.5),
            },
        })

    out["metric_primary"] = "r2"
    ranked = [m for m in out["models"] if not m["name"].startswith("baseline")]
    best = max(ranked, key=lambda m: m["metrics"]["r2"])
    out["best_model"] = best["name"]
    out["feature_importance"] = _importance(rf, feat_names)

    pred = rf.predict(Xte)
    actual = yte.to_numpy()
    out["residuals"] = [
        {"actual": _f(a), "predicted": _f(p)} for a, p in list(zip(actual, pred))[:300]
    ]
    return out


def _clustering(out, X, feat_names, spec, np):
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import silhouette_score

    k = int(spec.get("k") or 3)
    k = max(2, min(k, len(X) - 1))
    Xs = StandardScaler().fit_transform(X)
    km = KMeans(n_clusters=k, n_init=10, random_state=0)
    labels = km.fit_predict(Xs)
    sil = silhouette_score(Xs, labels) if len(set(labels)) > 1 else None

    out["metric_primary"] = "silhouette"
    out["best_model"] = f"kmeans (k={k})"
    sizes = {int(c): int((labels == c).sum()) for c in sorted(set(labels))}
    out["models"].append({"name": out["best_model"], "metrics": {"silhouette": _f(sil), "k": k}})
    out["cluster_sizes"] = sizes
    return out
