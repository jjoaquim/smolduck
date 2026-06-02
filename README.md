<div align="center">
  <img src="docs/blog/smolduck-logo.png" alt="smolduck" width="160" />
  <h1>smolduck</h1>
  <p><strong>A data analyst in a box.</strong> One command boots a disposable microVM running DuckDB plus a no-build browser workbench — SQL, charts, EDA, a Python scratchpad, and baseline ML. Untrusted code runs only inside the sandbox; your host is never touched.</p>
</div>

---

```bash
smolduck run ./sales      # boots a VM, registers every file, opens the workbench
# …analyze…
smolduck stop ./sales     # VM evaporates; your workspace folder is left intact
```

`smolduck run ./data` boots a [smolvm](https://smolvm.com) microVM in a couple hundred
milliseconds, mounts the folder, and opens a browser workbench. Every CSV / Parquet / JSON
file is already a queryable DuckDB view — no import step. You analyze; you stop; the box is
gone and nothing was installed or executed on your machine.

```
your host ─┬─ smolduck (the CLI — the only thing that runs on your machine)
           └─ microVM (disposable, no outbound network by default)
                ├─ DuckDB         your data + engine, persisted to the workspace
                ├─ Python kernel  pandas / polars / numpy / plotly / sklearn
                └─ workbench UI    Preact + DuckDB + Plotly, served from the VM
              ▲ untrusted code (the kernel, the AI analyst) runs HERE — never on your host
```

## Features

- **Folder → database.** Point at a directory; mixed CSV/Parquet/JSON are auto-registered as views.
- **SQL workbench.** Typed, sortable, filterable result grids with query timing; large results stream over a WebSocket.
- **Notebooks.** Ordered `sql` / `python` / `markdown` / `chart` cells, persisted as plain files and restored on relaunch.
- **Point-and-click charts.** bar / line / scatter / histogram / box / heatmap, with a **copy-as-code** button that emits the equivalent Plotly Python.
- **One-click EDA profile.** Per-column type, null %, exact distinct, μ/σ, numeric histograms, and a correlation matrix.
- **Sandboxed Python kernel.** Reads views into DataFrames, runs sklearn, renders Plotly inline — in a killable subprocess with a wall-clock timeout, **only inside the VM**.
- **Baseline ML.** Pick a target + features → models scored against a dummy baseline, with feature importance; every run logged to `experiments.jsonl`.
- **Optional AI analyst.** Pluggable LLM — a hosted **Anthropic** key or a local **Ollama** model → ask in natural language, get a **reviewed** SQL/Python cell (never auto-run). Not configured, not shown.
- **Export.** Notebook → self-contained HTML report (opens offline); results → CSV/Parquet; charts → PNG/SVG.
- **Disposable & reproducible.** Everything but your data is plain files in `.smolduck/`; stop and re-run restores sources, notebooks, and charts exactly.

## Requirements

- [**smolvm**](https://smolvm.com) on your `PATH` (the microVM runtime).
- [**Bun**](https://bun.sh) ≥ 1.3 — runs the CLI directly; no `bun install` needed.
- For development / building the image: [**uv**](https://docs.astral.sh/uv/) (Python 3.12) and network access.

The CLI is run via Bun. For brevity the examples below assume a shell alias:

```bash
alias smolduck='bun /path/to/smolduck/cli/src/index.ts'
# without the alias: bun cli/src/index.ts run ./data
```

## Quick start

```bash
# 1. (first time) bake the microVM image — backend + offline frontend + DuckDB extensions
smolduck build

# 2. boot the workbench against a folder of data
smolduck run ./sales              # opens http://127.0.0.1:4290 in your browser

# 3. when you're done
smolduck stop ./sales
```

`build` provisions a builder VM, installs the backend + deps (DuckDB, pandas, polars,
plotly, scikit-learn, …), vendors the frontend for offline use, and packs it into
`image/smolduck.smolmachine`. You only re-run it when the backend or frontend changes.

## Examples

These run against the bundled [`examples/ecommerce`](examples/ecommerce) dataset
(`smolduck run examples/ecommerce`) — `customers`, `orders`, and `refunds` are registered
automatically. See [Example datasets](#example-datasets) for a fuller, guided tour.

### Run SQL

In a SQL cell, write a query and press <kbd>⌘/Ctrl</kbd>+<kbd>Enter</kbd>:

```sql
SELECT segment, count(*) AS n
FROM customers
GROUP BY 1
ORDER BY 2 DESC;
```

You get a typed, sortable grid with the elapsed time. Clicking a source in the catalog
prefills `SELECT * FROM <view> LIMIT 100`.

### Build a chart, then copy it as code

Pick a chart type and map columns in the builder. Hit **copy as code** and it inserts the
equivalent Plotly cell — the clicks and the code are the same thing:

```python
df = sql("SELECT segment, count(*) n FROM customers GROUP BY 1 ORDER BY 2 DESC")
px.bar(df, x="segment", y="n")
```

### Python cell (sandboxed kernel)

A Python cell reads a view, runs sklearn, and renders a figure inline. `sql()`, `pd`, `pl`,
`np`, and `px` are pre-imported:

```python
import pandas as pd
from sklearn.linear_model import LinearRegression

df = sql("""
    SELECT o.items, c.segment, o.amount
    FROM orders o JOIN customers c USING (customer_id)
    WHERE o.status = 'completed'
""")
X = pd.get_dummies(df[["items", "segment"]], columns=["segment"])
model = LinearRegression().fit(X, df["amount"])
print(f"R² = {model.score(X, df['amount']):.2f}")          # ≈ 0.62 — basket size + segment
px.scatter(df, x="items", y="amount", color="segment", title="order value vs. basket size")
```

### Profile a dataset

Click the **▤** button on a source to open the EDA panel: per-column type, null %, distinct
count, numeric histograms, top-k for categoricals, and a pairwise correlation matrix.

### Run an ML experiment

Click **⚗** on a labeled source, pick a target + features + task, and run. You get baseline
models (vs. a dummy baseline), feature importance, and a confusion matrix / residuals —
logged to `.smolduck/experiments.jsonl`. Running it on the [churn](examples/churn) dataset
(target `churned`) produces a line like:

```jsonc
{"id":"…","task":"classification","target":"churned","best_model":"logistic regression",
 "models":[{"name":"baseline (most frequent)","metrics":{"accuracy":0.61,"f1_macro":0.38}},
           {"name":"logistic regression","metrics":{"accuracy":0.71,"f1_macro":0.69}},
           {"name":"random forest","metrics":{"accuracy":0.70,"f1_macro":0.68}}],
 "feature_importance":[{"feature":"tenure_months","importance":0.18}, …]}
```

### Ask the AI analyst (optional)

Configure a provider before launching; an "Ask" bar appears in the notebook. The analyst
explores the schema, writes and checks SQL, and **proposes** a cell for you to review and
run (never auto-run). The model is pluggable — pick one:

```bash
# hosted: Anthropic (BYO key, never persisted)
ANTHROPIC_API_KEY=sk-ant-… smolduck run examples/ecommerce

# local & private: Ollama (a tool-capable model, e.g. llama3.1 / qwen2.5-coder)
SMOLDUCK_LLM_PROVIDER=ollama SMOLDUCK_OLLAMA_MODEL=llama3.1 smolduck run examples/ecommerce
```

> _"which country has the most customers?"_ → proposes
> `SELECT country, count(*) AS n FROM customers GROUP BY 1 ORDER BY 2 DESC`

`smolduck run` forwards the relevant env vars into the VM. The agent makes only network
calls (its `run_python` tool still executes solely in the sandboxed kernel), so `smolduck
run` opens the narrowest VM egress that provider needs — and nothing when no analyst is
configured:

| Provider | Select with | Key knobs | VM egress |
|----------|-------------|-----------|-----------|
| Anthropic | `ANTHROPIC_API_KEY` set (auto), or `SMOLDUCK_LLM_PROVIDER=anthropic` | `SMOLDUCK_AGENT_MODEL` (default `claude-opus-4-8`) | `--allow-host api.anthropic.com` |
| Ollama | `SMOLDUCK_LLM_PROVIDER=ollama` | `SMOLDUCK_OLLAMA_HOST` (default `http://localhost:11434`), `SMOLDUCK_OLLAMA_MODEL` (default `llama3.1`) | `--outbound-localhost-only` — egress is confined to the **host loopback**, so the default `SMOLDUCK_OLLAMA_HOST=http://localhost:11434` reaches a daemon on your machine. The guest can't resolve the *name* `localhost`, so the backend rewrites it to `127.0.0.1`, which smolvm relays to the host's loopback |

> Egress opened for the analyst is also reachable by sandboxed code in the VM; it is kept
> as narrow as each provider allows (host loopback for Ollama, a single allowed host for
> Anthropic).

With nothing configured, the feature is invisible.

### Export a report

The **⤓ HTML** button exports the current notebook to a single self-contained HTML file
(Plotly inlined — opens offline, no server). Per-cell **⤓csv** / **⤓pq** download query
results; charts export to PNG/SVG from the Plotly modebar.

## Example datasets

Two ready-to-run datasets live under [`examples/`](examples/) — point smolduck at either
folder and every file is an instant DuckDB view. They're reproducible: regenerate them any
time with `uv run python examples/_generate.py` (seeded, stdlib-only).

### `examples/ecommerce` — relational (customers + orders + refunds)

220 customers, 3,600 orders, and the matching refunds — three files, so it shows
folder-as-database and joins.

```bash
smolduck run examples/ecommerce
```

**1 — average order value by segment** (a join across two views):

```sql
SELECT c.segment, count(*) AS orders, round(avg(o.amount), 2) AS avg_amount
FROM orders o JOIN customers c USING (customer_id)
WHERE o.status = 'completed'
GROUP BY 1 ORDER BY avg_amount DESC;
```

> `enterprise` ≈ $389 · `smb` ≈ $124 · `consumer` ≈ $44 — segment drives basket size.

**2 — revenue over time, and the shift to mobile** (run, then **chart → line**, x=`month`):

```sql
SELECT strftime(CAST(order_date AS DATE), '%Y-%m')                                AS month,
       round(sum(amount), 0)                                                      AS revenue,
       round(100.0 * sum(amount) FILTER (WHERE channel = 'mobile') / sum(amount), 1) AS mobile_pct
FROM orders WHERE status = 'completed'
GROUP BY 1 ORDER BY 1;
```

> Mobile's share of revenue climbs from ~13% in January to ~40% by December. Hit **copy as
> code** on the chart to drop the equivalent `px.line(...)` into a Python cell.

**3 — where refunds come from**:

```sql
SELECT r.reason, count(*) AS n, round(sum(r.amount), 2) AS refunded
FROM refunds r JOIN orders o USING (order_id)
GROUP BY 1 ORDER BY refunded DESC;
```

Then click **▤** on `orders` to profile it — null %, distinct counts, an `amount` histogram
(long right tail), and the `items`↔`amount` correlation.

### `examples/churn` — one wide, labeled table

1,500 subscribers in `subscriptions.csv` with a binary `churned` label — built for profiling,
correlation, and the ML panel.

```bash
smolduck run examples/churn
```

**1 — profile first.** Click **▤** on `subscriptions`: the correlation matrix shows `churned`
rising with `support_tickets` and `monthly_charges` and falling with `tenure_months`.

**2 — slice the driver** (run, then **chart → bar**, x=`contract`, y=`churn_pct`):

```sql
SELECT contract, count(*) AS n, round(100.0 * avg(churned), 1) AS churn_pct
FROM subscriptions GROUP BY 1 ORDER BY churn_pct DESC;
```

> `month-to-month` ≈ 55% churn vs `two-year` ≈ 14% — contract type is the strongest lever.

**3 — train a baseline model.** Click **⚗** on `subscriptions`, target `churned`, task
**classification**, and run. The models land around **0.71 accuracy** (logistic regression,
just ahead of random forest) against a **0.60** most-frequent baseline, and feature
importance puts `tenure_months`, `monthly_charges`, and `support_tickets` on top — every run
appended to `.smolduck/experiments.jsonl`.

With the [AI analyst](#ask-the-ai-analyst-optional) configured, just ask
_"what predicts churn here?"_ and review the cell it proposes.

## CLI

```
smolduck run [path]      boot the workbench against a workspace folder (default: .)
smolduck stop [path]     stop the running session; the workspace is left intact
smolduck status [path]   show the running session for a workspace
smolduck build           (re)build the microVM image + pack

Flags (run):
  --port <n>     UI port (default: 4290)
  --no-open      do not open a browser
  --readonly     mount the workspace read-only (session artifacts are ephemeral)
  --mem <size>   microVM memory (e.g. 2g, 2048m)
  --cpus <n>     microVM vCPUs
```

## Data model

Your data lives in DuckDB; everything else is a plain, git-friendly file in
`<workspace>/.smolduck/` — so a workspace fully reconstructs from disk:

| File | Contents |
|------|----------|
| `manifest.json` | version, created_at, registered sources, settings |
| `notebooks/*.json` | ordered cells (`sql`/`python`/`markdown`/`chart`) + cached results |
| `charts/*.json` | pinned Plotly spec + originating query + title |
| `experiments.jsonl` | one ML run per line |
| `store.duckdb` | the DuckDB database (views + materialized tables) |

## Security model

The hard rule: **untrusted code only ever runs inside the microVM.** The Python kernel and
the AI analyst's `run_python` tool are gated by `SMOLDUCK_IN_VM` (set by the VM entrypoint)
and are **off on the host** unless a developer explicitly opts in with
`SMOLDUCK_ALLOW_HOST_KERNEL=1` for native development. The VM has no outbound network by
default; `smolduck stop` deletes the VM, leaving zero processes or files on your host
outside the workspace folder.

## Development

The backend runs natively for a fast inner loop; the VM just wraps the backend unchanged.

```bash
# backend (native), pointed at a workspace
cd backend
SMOLDUCK_WORKSPACE=../fixtures SMOLDUCK_FRONTEND_DIR=../frontend \
  uv run uvicorn app.main:app

# to exercise the kernel/ML/agent natively (opt-in, host execution):
#   SMOLDUCK_ALLOW_HOST_KERNEL=1   SMOLDUCK_AGENT_FAKE=1

# tests
cd backend && uv run pytest        # 56 cases
```

The frontend is no-build: Preact + htm + CodeMirror 6 + Plotly via a pinned ESM import map
(CDN in native dev, self-hosted in the VM image). No bundler.

## Layout

```
backend/    FastAPI + DuckDB engine, Python kernel, ML, agent, export  (runs in the VM)
frontend/   no-build Preact + htm SPA (notebook, charts, profile, ML, agent panels)
cli/        the `smolduck` command (TypeScript on Bun) — boot/stop/status/build
image/      provision.sh, entrypoint.sh, vendor_assets.py — bake the microVM image
examples/   ready-to-run demo datasets (ecommerce, churn) + their generator
fixtures/   small sample data used by the test suite (customers/orders/refunds)
docs/       the blog post and logo
```

## Status

**MVP (Phases 1–2) complete** — verified end-to-end against the real microVM, host-clean
teardown confirmed, and the full backend test suite green. Phase 3 (smolfleet parallel
scale-out) is deferred until the smolfleet job/NATS interface is available.

> `smolduck` is a working name (it sits between smolvm/smolfleet and DuckDB).

## License

MIT — see [LICENSE](LICENSE).
