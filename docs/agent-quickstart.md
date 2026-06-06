# Agent quickstart — drive smolduck over MCP

This is the five-minute "magic moment": an AI agent (Claude Code/Desktop, Cursor,
or your own) analyzes a folder of data end-to-end — registering sources, querying,
charting, and exporting a report — with **every line of agent-generated code
running inside smolduck's disposable microVM, never on your host.**

The agent brings the model; smolduck brings the engine *and* the sandbox.

## 1. Start a session

```bash
smolduck run ./your-data        # boots the microVM, writes ./your-data/.smolduck/session.json
```

Leave it running. The MCP server attaches to this session (it reads the session
file for the backend port). You don't need the browser open — the backend is live
either way.

## 2. Point your MCP client at it

`claude_desktop_config.json` (or any client's MCP config):

```json
{
  "mcpServers": {
    "smolduck": {
      "command": "uvx",
      "args": ["--from", "/abs/path/to/smolduck/mcp", "smolduck-mcp",
               "--workspace", "/abs/path/to/your-data"]
    }
  }
}
```

For a native (no-VM) dev backend, skip the session file: `--url http://127.0.0.1:8000`.

## 3. The end-to-end recipe

Ask the agent something like *"Profile this data, then chart revenue by month and
save me a report."* Under the hood it strings together **resources** (read state by
URI) and **tools** (act):

1. **Discover** — read `smolduck://sources` to see what's registered. If a file
   isn't a source yet, call `register_source("sales.csv")`.
2. **Understand** — read `smolduck://schema/sales` (a DESCRIBE) so it uses real
   column names, then `profile_source("<id>")` for null %, distincts, correlations.
3. **Query** — `query_sql("SELECT date_trunc('month', ts) AS month, sum(amount) AS revenue FROM sales GROUP BY 1 ORDER BY 1")`
   to explore and verify, preview-capped.
4. **Verify harder** — `run_python("import pandas as pd; df = sql('SELECT * FROM sales'); print(df.describe())")`.
   This Python runs **in the microVM**, not on your machine.
5. **Chart** — `create_chart(query, config={"type": "line", "x": "month", "y": "revenue"}, spec={...})`
   pins a Plotly chart artifact.
6. **Compose** — `save_notebook(cells=[...], title="Revenue review")` lays the SQL,
   chart, and prose into a notebook a human can reopen in the workbench.
7. **Deliver** — `export_report(notebook_id)` writes a self-contained HTML report;
   `export_data(sql, format="parquet")` writes the underlying data.

At any point the agent can re-read `smolduck://notebook/{id}` to see exactly what it
built (cells + cached results) before refining.

## 4. Re-run it headless

Once the notebook exists, refresh it against updated data without the UI:

```bash
smolduck replay <notebook-id> --out report.html
```

Cells (including Python) re-execute inside the live microVM and the report is
regenerated — CI-friendly, no browser.

If the analysis builds on **managed tables** (`CREATE TABLE lake.…` or a
materialized query), add `--reproduce` to pin those reads to the DuckLake snapshot
the notebook recorded, so the run reproduces *exactly* even after the data changed:

```bash
smolduck replay <notebook-id> --reproduce --out report.html
```

## Why this is safe

`run_python`, `run_ml_experiment`, and `replay` all execute inside the microVM,
gated by `SMOLDUCK_IN_VM`. The VM has no outbound network by default, and
`smolduck stop` deletes it — leaving zero processes or files on your host outside
the workspace folder. The agent can run whatever code it likes; the blast radius is
the box.
