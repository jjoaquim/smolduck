import { html } from "htm/preact";
import { PlotlyFigure } from "./cells/PlotlyFigure.js";

// Overlay gallery of pinned charts (persisted under .smolduck/charts/). Proves
// pinned charts survive relaunch — they reload from the backend on mount.

export function PinnedCharts({ charts, onClose, onDelete }) {
  return html`
    <div class="pinned-overlay" onClick=${onClose}>
      <div class="pinned-panel" onClick=${(e) => e.stopPropagation()}>
        <div class="pinned-head">
          <span class="pinned-title">Pinned charts (${charts.length})</span>
          <button class="cell-ctl" title="close" onClick=${onClose}>✕</button>
        </div>
        ${charts.length === 0
          ? html`<div class="rail-empty">No pinned charts yet. Build a chart cell and hit 📌 pin.</div>`
          : html`<div class="pinned-grid">
              ${charts.map(
                (c) => html`<div class="pinned-card" key=${c.id} data-testid="pinned-card">
                  <div class="pinned-card-head">
                    <span class="pinned-card-title">${c.title}</span>
                    <button class="cell-ctl cell-del" title="remove" onClick=${() => onDelete(c.id)}>✕</button>
                  </div>
                  ${c.spec && c.spec.data
                    ? html`<${PlotlyFigure} figure=${c.spec} />`
                    : html`<div class="rail-empty">no spec</div>`}
                </div>`
              )}
            </div>`}
      </div>
    </div>
  `;
}
