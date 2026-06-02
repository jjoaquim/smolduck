import { html } from "htm/preact";
import { useMemo } from "preact/hooks";
import { CHART_TYPES, buildFigure } from "../../lib/chart.js";
import { PlotlyFigure } from "./PlotlyFigure.js";

const ENC_LABEL = { x: "X", y: "Y", color: "Color", z: "Z (value)" };
const OPTIONAL = new Set(["color"]);

export function ChartBuilder({ result, config, running, error, onConfigChange, onCopyAsCode, onPin }) {
  const cfg = config && config.type ? config : { type: "bar", ...(config || {}) };
  const columns = result ? result.columns : [];
  const typeMeta = CHART_TYPES.find((t) => t.id === cfg.type) || CHART_TYPES[0];

  const figure = useMemo(
    () => (result ? buildFigure(result.columns, result.rows, cfg) : null),
    [result, cfg.type, cfg.x, cfg.y, cfg.color, cfg.z, cfg.title]
  );

  const set = (patch) => onConfigChange({ ...cfg, ...patch });

  if (error) {
    return html`<div class="cell-result"><div class="result-error">⚠ ${error}</div></div>`;
  }
  if (!result) {
    return html`<div class="chart-empty">
      ${running ? "running query…" : "Write a SQL query above and Run it, then build a chart."}
    </div>`;
  }

  return html`
    <div class="chart-builder" data-testid="chart-builder">
      <div class="chart-controls">
        <label class="chart-enc">
          <span>Type</span>
          <select value=${cfg.type} onChange=${(e) => set({ type: e.target.value })}>
            ${CHART_TYPES.map((t) => html`<option key=${t.id} value=${t.id}>${t.label}</option>`)}
          </select>
        </label>
        ${typeMeta.enc.map(
          (enc) => html`<label class="chart-enc" key=${enc}>
            <span>${ENC_LABEL[enc]}</span>
            <select value=${cfg[enc] || ""} onChange=${(e) => set({ [enc]: e.target.value || undefined })}>
              ${OPTIONAL.has(enc) && html`<option value="">—</option>`}
              ${!OPTIONAL.has(enc) && html`<option value="">choose…</option>`}
              ${columns.map((c) => html`<option key=${c.name} value=${c.name}>${c.name}</option>`)}
            </select>
          </label>`
        )}
        <label class="chart-enc chart-title-enc">
          <span>Title</span>
          <input
            type="text"
            value=${cfg.title || ""}
            placeholder="(optional)"
            onInput=${(e) => set({ title: e.target.value || undefined })}
          />
        </label>
        <span class="cell-spacer"></span>
        <button class="btn-ghost" onClick=${onCopyAsCode} title="insert an equivalent Python cell">⧉ copy as code</button>
        <button class="btn-ghost chart-pin" onClick=${() => onPin(figure)} disabled=${!figure} title="pin this chart">📌 pin</button>
      </div>
      ${figure
        ? html`<${PlotlyFigure} figure=${figure} />`
        : html`<div class="chart-empty">Pick the encodings above to draw the chart.</div>`}
    </div>
  `;
}
