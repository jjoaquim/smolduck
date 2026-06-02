import { html } from "htm/preact";
import { ResultsGrid } from "../ResultsGrid.js";
import { PlotlyFigure } from "./PlotlyFigure.js";

// Render the aggregated output of a Python cell: streamed stdout/stderr, inline
// Plotly figures, a DataFrame preview, the last-expression repr, and errors.
// `out` is the live accumulator while running, or the cell's cached last_result.

function dfToResult(df) {
  // Reuse ResultsGrid by shaping the kernel's DataFrame preview like a query result.
  return {
    columns: df.columns.map((name) => ({ name, type: "" })),
    rows: df.rows,
    row_count: df.shape ? df.shape[0] : df.rows.length,
    truncated: !!df.truncated,
    limit: df.rows.length,
    elapsed_ms: 0,
  };
}

export function PythonOutput({ out, running }) {
  if (!out && !running) return null;
  const o = out || {};
  const empty =
    !o.stdout && !o.stderr && !o.error && !(o.figures && o.figures.length) && !o.dataframe && o.result == null;

  return html`
    <div class="py-output" data-testid="py-output">
      ${running && empty && html`<div class="py-running">running…</div>`}
      ${o.stdout && html`<pre class="py-stream py-stdout">${o.stdout}</pre>`}
      ${o.stderr && html`<pre class="py-stream py-stderr">${o.stderr}</pre>`}
      ${o.error && html`<pre class="py-error" data-testid="py-error">⚠ ${o.error}</pre>`}
      ${(o.figures || []).map((fig, i) => html`<${PlotlyFigure} key=${i} figure=${fig} />`)}
      ${o.dataframe && html`<div class="py-df"><${ResultsGrid} result=${dfToResult(o.dataframe)} /></div>`}
      ${o.result != null && html`<pre class="py-result">${o.result}</pre>`}
    </div>
  `;
}
