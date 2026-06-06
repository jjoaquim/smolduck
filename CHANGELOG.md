# Changelog

All notable changes to smolduck are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/).

## [0.2.0]

### Added

- **Visible sandbox.** The egress boundary the microVM already enforces is now
  surfaced: `smolduck run` prints the sandbox egress policy at boot (`offline`,
  host-loopback for Ollama, or `api.anthropic.com` only), the workbench shows a
  live egress badge with a running count of the analyst's outbound calls (logged
  to `.smolduck/egress.jsonl`), `GET /api/agent/egress` exposes the same data, and
  `smolduck stop` prints a teardown proof of what was destroyed versus what
  persists.
- **Headless replay.** `smolduck replay <notebook> [--out report.html]` re-runs a
  saved notebook's cells against the running session and regenerates its outputs
  (and, optionally, a self-contained HTML report) with no browser — backed by
  `POST /api/notebooks/{id}/replay`. Python cells stay VM-gated.
- **MCP resources.** External agents can now *read* workspace state by URI —
  `smolduck://sources`, `smolduck://notebooks`, `smolduck://charts`,
  `smolduck://notebook/{id}`, `smolduck://chart/{id}`, and
  `smolduck://schema/{view}` — alongside the existing tools. A new
  [agent quickstart](docs/agent-quickstart.md) walks through driving smolduck
  end-to-end over MCP.
- **Command palette.** Press `Ctrl`/`Cmd`-`K` for a quick action launcher that
  also surfaces recent queries; selecting one drops it into the notebook as a new
  cell.
- **Query history.** SQL runs are recorded per workspace (`GET/POST/DELETE
  /api/history`) and feed the command palette.
- **Example dataset loader.** An empty workspace offers a one-click "Load example
  data" button; the demo dataset is generated in-code (no download), so it works
  in the offline microVM.

### Changed

- **Clearer SQL errors.** Query errors now lead with a concise `line N, column M`
  header and preserve DuckDB's caret pointer and candidate-name hints, rendered
  monospaced in the result panel.

## [0.1.0]

- Initial release: disposable microVM running DuckDB plus a no-build browser
  workbench — SQL, charts, EDA, a sandboxed Python scratchpad, baseline ML, an
  optional AI analyst, and an MCP server for external agents.
