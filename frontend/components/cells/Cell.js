import { html } from "htm/preact";
import { CellEditor } from "./CellEditor.js";
import { ResultsGrid } from "../ResultsGrid.js";
import { PythonOutput } from "./PythonOutput.js";
import { ChartBuilder } from "./ChartBuilder.js";
import { renderMarkdown } from "../../lib/markdown.js";
import { downloadPost } from "../../lib/api.js";

const KINDS = ["sql", "python", "markdown", "chart"];
const KIND_LABEL = { sql: "SQL", python: "Python", markdown: "Markdown", chart: "Chart" };
const CODE_LANG = { sql: "sql", python: "text", markdown: "text", chart: "text" };

export function Cell({
  cell,
  catalog,
  index,
  total,
  running,
  error,
  kernelEnabled,
  pyout,
  onChange,
  onRun,
  onKindChange,
  onMoveUp,
  onMoveDown,
  onDelete,
  onConfigChange,
  onCopyAsCode,
  onPin,
}) {
  const runnable =
    cell.kind === "sql" || cell.kind === "chart" || (cell.kind === "python" && kernelEnabled);
  const elapsed =
    cell.last_result && !error && cell.last_result.elapsed_ms != null ? cell.last_result.elapsed_ms : null;

  return html`
    <section class="cell cell-${cell.kind}" data-testid="cell">
      <div class="cell-head">
        <select
          class="cell-kind-select"
          value=${cell.kind}
          onChange=${(e) => onKindChange(e.target.value)}
          title="cell type"
        >
          ${KINDS.map((k) => html`<option key=${k} value=${k}>${KIND_LABEL[k]}</option>`)}
        </select>
        ${runnable && html`<span class="cell-hint">⌘/Ctrl+Enter</span>`}
        <span class="cell-spacer"></span>
        ${elapsed != null && html`<span class="cell-timing">${elapsed} ms</span>`}
        ${cell.kind === "sql" &&
        cell.last_result &&
        cell.last_result.columns &&
        cell.last_result.columns.length > 0 &&
        html`<button class="cell-dl" title="download result as CSV"
            onClick=${() => downloadPost("/api/export/data", { sql: cell.source, format: "csv" }, "result.csv")}>⤓csv</button>
          <button class="cell-dl" title="download result as Parquet"
            onClick=${() => downloadPost("/api/export/data", { sql: cell.source, format: "parquet" }, "result.parquet")}>⤓pq</button>`}
        ${runnable &&
        html`<button class="btn run-btn" data-testid="run" onClick=${onRun} disabled=${running}>
          ${running ? "running…" : "Run ▸"}
        </button>`}
        <button class="cell-ctl" title="move up" onClick=${onMoveUp} disabled=${index === 0}>↑</button>
        <button class="cell-ctl" title="move down" onClick=${onMoveDown} disabled=${index === total - 1}>↓</button>
        <button class="cell-ctl cell-del" title="delete cell" onClick=${onDelete}>✕</button>
      </div>

      <${CellEditor}
        value=${cell.source}
        language=${CODE_LANG[cell.kind]}
        catalog=${catalog}
        onChange=${onChange}
        onRun=${runnable ? onRun : undefined}
      />

      ${cell.kind === "sql" &&
      (error || cell.last_result || running) &&
      html`<div class="cell-result">
        <${ResultsGrid} result=${error ? null : cell.last_result} error=${error} running=${running} />
      </div>`}

      ${cell.kind === "python" &&
      kernelEnabled &&
      html`<${PythonOutput} out=${running ? pyout : cell.last_result} running=${running} />`}

      ${cell.kind === "python" &&
      !kernelEnabled &&
      html`<div class="cell-note">The Python kernel runs untrusted code and is only enabled inside the
        smolduck microVM (run via <code>smolduck run</code>).</div>`}

      ${cell.kind === "markdown" &&
      html`<div
        class="md-render"
        dangerouslySetInnerHTML=${{ __html: renderMarkdown(cell.source) || '<span class="md-empty">empty markdown cell</span>' }}
      ></div>`}

      ${cell.kind === "chart" &&
      html`<${ChartBuilder}
        result=${error ? null : cell.last_result}
        config=${cell.config}
        running=${running}
        error=${error}
        onConfigChange=${onConfigChange}
        onCopyAsCode=${onCopyAsCode}
        onPin=${onPin}
      />`}
    </section>
  `;
}
