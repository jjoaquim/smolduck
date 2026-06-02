import { html } from "htm/preact";
import { useMemo, useState } from "preact/hooks";
import { api } from "../lib/api.js";
import { PlotlyFigure } from "./cells/PlotlyFigure.js";

// Right-panel ML experiment runner: pick target + features + task, run baseline
// models in the sandboxed kernel, and view metrics / feature importance /
// confusion matrix or residuals. Each run is logged to experiments.jsonl.

const TASKS = ["auto", "classification", "regression", "clustering"];

function MetricsTable({ run }) {
  const keys = run.models.length ? Object.keys(run.models[0].metrics) : [];
  return html`<table class="ml-metrics">
    <thead>
      <tr>
        <th>model</th>
        ${keys.map((k) => html`<th key=${k}>${k}</th>`)}
      </tr>
    </thead>
    <tbody>
      ${run.models.map(
        (m) => html`<tr key=${m.name} class=${m.name === run.best_model ? "ml-best" : ""}>
          <td>${m.name}</td>
          ${keys.map((k) => html`<td key=${k}>${m.metrics[k] == null ? "—" : m.metrics[k]}</td>`)}
        </tr>`
      )}
    </tbody>
  </table>`;
}

function Importance({ items }) {
  const top = items.slice(0, 12);
  const max = Math.max(1e-9, ...top.map((t) => t.importance || 0));
  return html`<ul class="pf-topk">
    ${top.map(
      (t) => html`<li key=${t.feature} class="pf-topk-row">
        <span class="pf-topk-val" title=${t.feature}>${t.feature}</span>
        <span class="pf-topk-bar" style=${`width:${((t.importance || 0) / max) * 100}%`}></span>
        <span class="pf-topk-count">${(t.importance || 0).toFixed(3)}</span>
      </li>`
    )}
  </ul>`;
}

function ConfusionMatrix({ cm }) {
  const max = Math.max(1, ...cm.matrix.flat());
  return html`<table class="pf-corr ml-cm">
    <thead>
      <tr>
        <th title="actual \\ predicted">a\\p</th>
        ${cm.labels.map((l) => html`<th key=${l} title=${l}>${l}</th>`)}
      </tr>
    </thead>
    <tbody>
      ${cm.matrix.map(
        (row, i) => html`<tr key=${i}>
          <th title=${cm.labels[i]}>${cm.labels[i]}</th>
          ${row.map(
            (v, j) => html`<td key=${j} style=${`background:rgba(84,199,176,${((v / max) * 0.8).toFixed(2)})`}>
              ${v}
            </td>`
          )}
        </tr>`
      )}
    </tbody>
  </table>`;
}

function residualsFigure(residuals) {
  const actual = residuals.map((r) => r.actual);
  const predicted = residuals.map((r) => r.predicted);
  const lo = Math.min(...actual, ...predicted);
  const hi = Math.max(...actual, ...predicted);
  return {
    data: [
      { type: "scatter", mode: "markers", x: actual, y: predicted, marker: { size: 6 }, name: "test" },
      { type: "scatter", mode: "lines", x: [lo, hi], y: [lo, hi], line: { dash: "dot" }, name: "ideal" },
    ],
    layout: { showlegend: false, xaxis: { title: { text: "actual" } }, yaxis: { title: { text: "predicted" } } },
  };
}

export function MlPanel({ source, kernelEnabled, onClose }) {
  const cols = (source.columns || []).map((c) => c.name);
  const [target, setTarget] = useState(cols[cols.length - 1] || "");
  const [task, setTask] = useState("auto");
  const [checked, setChecked] = useState(() => new Set(cols));
  const [running, setRunning] = useState(false);
  const [run, setRun] = useState(null);
  const [error, setError] = useState(null);

  const isClustering = task === "clustering";
  const features = useMemo(
    () => cols.filter((c) => checked.has(c) && (isClustering || c !== target)),
    [checked, target, isClustering]
  );

  const toggle = (c) => {
    setChecked((s) => {
      const n = new Set(s);
      n.has(c) ? n.delete(c) : n.add(c);
      return n;
    });
  };

  async function runExperiment() {
    setRunning(true);
    setError(null);
    try {
      const body = {
        source_id: source.id,
        target: isClustering ? null : target,
        features,
        task,
      };
      setRun(await api.runExperiment(body));
    } catch (e) {
      setError(e.message);
      setRun(null);
    } finally {
      setRunning(false);
    }
  }

  return html`
    <aside class="profile-panel ml-panel" data-testid="ml-panel">
      <div class="pf-head">
        <span class="pf-title">ML · ${source.view_name}</span>
        <button class="cell-ctl" title="close" onClick=${onClose}>✕</button>
      </div>

      ${!kernelEnabled &&
      html`<div class="cell-note">Experiments run in the sandbox kernel — only inside the smolduck microVM
        (run via <code>smolduck run</code>).</div>`}

      <div class="ml-config">
        <label class="chart-enc">
          <span>Task</span>
          <select value=${task} onChange=${(e) => setTask(e.target.value)}>
            ${TASKS.map((t) => html`<option key=${t} value=${t}>${t}</option>`)}
          </select>
        </label>
        ${!isClustering &&
        html`<label class="chart-enc">
          <span>Target</span>
          <select value=${target} onChange=${(e) => setTarget(e.target.value)}>
            ${cols.map((c) => html`<option key=${c} value=${c}>${c}</option>`)}
          </select>
        </label>`}
        <div class="ml-features">
          <span class="ml-features-label">Features</span>
          ${cols.map(
            (c) => html`<label key=${c} class=${"ml-feat " + (!isClustering && c === target ? "ml-feat-disabled" : "")}>
              <input
                type="checkbox"
                checked=${checked.has(c) && (isClustering || c !== target)}
                disabled=${!isClustering && c === target}
                onChange=${() => toggle(c)}
              />
              ${c}
            </label>`
          )}
        </div>
        <button
          class="btn ml-run"
          data-testid="ml-run"
          onClick=${runExperiment}
          disabled=${running || !kernelEnabled || features.length === 0}
        >
          ${running ? "training…" : "Run experiment ▸"}
        </button>
      </div>

      ${error && html`<div class="result-error">⚠ ${error}</div>`}
      ${run &&
      html`<div class="ml-results" data-testid="ml-results">
        <div class="pf-meta">
          <b>${run.best_model}</b> · ${run.task} · ${run.n_rows} rows · ${run.n_features} features
        </div>
        <div class="pf-section-title">Models</div>
        <${MetricsTable} run=${run} />
        ${run.feature_importance &&
        run.feature_importance.length > 0 &&
        html`<div class="pf-section-title">Feature importance</div><${Importance} items=${run.feature_importance} />`}
        ${run.confusion_matrix &&
        html`<div class="pf-section-title">Confusion matrix</div><${ConfusionMatrix} cm=${run.confusion_matrix} />`}
        ${run.residuals &&
        run.residuals.length > 0 &&
        html`<div class="pf-section-title">Actual vs predicted</div>
        <${PlotlyFigure} figure=${residualsFigure(run.residuals)} />`}
        ${run.cluster_sizes &&
        html`<div class="pf-section-title">Cluster sizes</div>
        <div class="pf-meta">${Object.entries(run.cluster_sizes).map(([k, v]) => `#${k}: ${v}`).join("  ·  ")}</div>`}
      </div>`}
    </aside>
  `;
}
