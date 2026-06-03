# smolduck-mcp

An [MCP](https://modelcontextprotocol.io) server that lets **any MCP-capable agent**
(Claude Desktop/Code, Cursor, your own) drive a running smolduck session — query
data, run sandboxed Python, build charts, run baseline ML, and export reports.

The agent supplies the model; smolduck supplies the data engine **and the safety
boundary**: `run_python` and ML execute inside smolduck's disposable microVM, never
on your host. This server is a thin, host-side client of the smolduck backend — it
runs no untrusted code itself.

## Prerequisites

A **running smolduck session** for the workspace you want to analyze:

```bash
smolduck run ./your-data        # writes ./your-data/.smolduck/session.json
```

The MCP server reads that session file to find the backend port (or pass `--url`).

## Wire it into an MCP client

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

`--workspace` locates `.smolduck/session.json`. For a backend running without the VM
(native dev), skip the session file and point straight at it: `--url http://127.0.0.1:8000`.

## Tools

Read / analyze:
- `smolduck_status` — session health + whether the sandboxed kernel is available
- `list_sources`, `get_schema(view)`, `query_sql(sql, limit?)`, `profile_source(source_id)`
- `run_python(code, timeout?)` — sandboxed kernel (`pd`, `pl`, `np`, `px`, `sql()`)

Create artifacts (no deletes):
- `register_source(path, view_name?)`
- `save_notebook(cells, title?)` — cells are `{kind, source, config?}`
- `create_chart(query, config, spec, title?)` — caller provides the Plotly `spec`
- `run_ml_experiment(source_id, features, target?, task?, test_size?, k?)`
- `export_report(notebook_id, out_path?)`, `export_data(sql, format?, out_path?)`

## Security

The smolduck backend is **unauthenticated** on its port. This server only ever talks
to `127.0.0.1` (a local session). Do not expose it remotely without first adding real
auth to the backend.

## Develop

```bash
cd mcp
uv run pytest                                   # unit tests (mocked HTTP)
uv run python -m smolduck_mcp.server --help     # run from source
```
