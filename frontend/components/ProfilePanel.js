import { html } from "htm/preact";
import { typeIcon } from "../lib/format.js";

// Right-panel EDA profile: per-column type/null%/distinct + a mini numeric
// histogram or top-k bars, plus a pairwise correlation matrix. Rendered with
// plain divs (no Plotly) to stay light.

function fmtNum(v) {
  if (v == null) return "—";
  if (Number.isInteger(v)) return String(v);
  return (+v.toFixed(4)).toString();
}

function MiniHistogram({ histogram }) {
  const bins = histogram.bins || [];
  const max = Math.max(1, ...bins.map((b) => b.count));
  return html`<div class="pf-hist" title="distribution">
    ${bins.map(
      (b, i) => html`<div
        key=${i}
        class="pf-hist-bar"
        style=${`height:${Math.max(2, (b.count / max) * 100)}%`}
        title=${`${fmtNum(b.lo)} – ${fmtNum(b.hi)}: ${b.count}`}
      ></div>`
    )}
  </div>`;
}

function TopK({ topK }) {
  const max = Math.max(1, ...topK.map((t) => t.count));
  return html`<ul class="pf-topk">
    ${topK.map(
      (t, i) => html`<li key=${i} class="pf-topk-row">
        <span class="pf-topk-val" title=${t.value == null ? "NULL" : String(t.value)}>
          ${t.value == null ? html`<span class="null">NULL</span>` : String(t.value)}
        </span>
        <span class="pf-topk-bar" style=${`width:${(t.count / max) * 100}%`}></span>
        <span class="pf-topk-count">${t.count}</span>
      </li>`
    )}
  </ul>`;
}

function ColumnCard({ col }) {
  return html`<div class="pf-col" data-testid="pf-col">
    <div class="pf-col-head">
      <span class="type-icon type-${col.kind}" title=${col.type}>${typeIcon(col.type)}</span>
      <span class="pf-col-name">${col.name}</span>
      <span class="pf-col-type">${col.type}</span>
    </div>
    <div class="pf-col-stats">
      <span><b>${col.distinct}</b> distinct</span>
      <span class=${col.null_pct > 0 ? "pf-nulls" : ""}><b>${col.null_pct}%</b> null</span>
      ${col.kind === "numeric" &&
      col.avg != null &&
      html`<span>μ <b>${fmtNum(col.avg)}</b></span><span>σ <b>${fmtNum(col.std)}</b></span>`}
      ${col.min != null && html`<span class="pf-minmax">${col.min} → ${col.max}</span>`}
    </div>
    ${col.histogram && html`<${MiniHistogram} histogram=${col.histogram} />`}
    ${col.top_k && col.top_k.length > 0 && html`<${TopK} topK=${col.top_k} />`}
  </div>`;
}

function corrColor(v) {
  if (v == null) return "transparent";
  const rgb = v >= 0 ? "84,199,176" : "232,116,107";
  return `rgba(${rgb},${(Math.abs(v) * 0.75).toFixed(2)})`;
}

function Correlation({ correlation }) {
  const { columns, matrix } = correlation;
  return html`<div class="pf-section">
    <div class="pf-section-title">Correlation</div>
    <div class="pf-corr-scroll">
      <table class="pf-corr">
        <thead>
          <tr>
            <th></th>
            ${columns.map((c) => html`<th key=${c} title=${c}>${c}</th>`)}
          </tr>
        </thead>
        <tbody>
          ${matrix.map(
            (row, i) => html`<tr key=${i}>
              <th title=${columns[i]}>${columns[i]}</th>
              ${row.map(
                (v, j) => html`<td key=${j} style=${`background:${corrColor(v)}`} title=${v == null ? "—" : v}>
                  ${v == null ? "" : (+v.toFixed(2)).toString()}
                </td>`
              )}
            </tr>`
          )}
        </tbody>
      </table>
    </div>
  </div>`;
}

export function ProfilePanel({ profile, loading, error, onClose }) {
  return html`
    <aside class="profile-panel" data-testid="profile-panel">
      <div class="pf-head">
        <span class="pf-title">Profile${profile ? html` · ${profile.view_name}` : ""}</span>
        <button class="cell-ctl" title="close" onClick=${onClose}>✕</button>
      </div>
      ${loading && html`<div class="rail-empty">profiling…</div>`}
      ${error && html`<div class="result-error">⚠ ${error}</div>`}
      ${profile &&
      !loading &&
      html`<div class="pf-body">
        <div class="pf-meta"><b>${profile.row_count}</b> rows · ${profile.columns.length} columns</div>
        ${profile.correlation && html`<${Correlation} correlation=${profile.correlation} />`}
        <div class="pf-section">
          <div class="pf-section-title">Columns</div>
          ${profile.columns.map((c) => html`<${ColumnCard} key=${c.name} col=${c} />`)}
        </div>
      </div>`}
    </aside>
  `;
}
