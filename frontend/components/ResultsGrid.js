import { html } from "htm/preact";
import { useMemo, useState } from "preact/hooks";
import { typeIcon, isNumericKind, fmtCell } from "../lib/format.js";

export function ResultsGrid({ result, error, running }) {
  const [sort, setSort] = useState({ col: null, dir: 1 });
  const [filter, setFilter] = useState("");

  const columns = result ? result.columns : [];

  const view = useMemo(() => {
    if (!result) return [];
    let rows = result.rows;
    const q = filter.trim().toLowerCase();
    if (q) {
      rows = rows.filter((r) => r.some((v) => fmtCell(v).toLowerCase().includes(q)));
    }
    if (sort.col !== null) {
      const i = sort.col;
      rows = rows.slice().sort((a, b) => {
        const x = a[i], y = b[i];
        if (x === null || x === undefined) return 1;
        if (y === null || y === undefined) return -1;
        if (x < y) return -1 * sort.dir;
        if (x > y) return 1 * sort.dir;
        return 0;
      });
    }
    return rows;
  }, [result, filter, sort]);

  function toggleSort(i) {
    setSort((s) => (s.col === i ? { col: i, dir: -s.dir } : { col: i, dir: 1 }));
  }

  if (error) {
    return html`<div class="result-error" data-testid="query-error">⚠ ${error}</div>`;
  }
  if (!result) {
    return html`<div class="result-empty">
      ${running ? "running…" : "Run a query to see results."}
    </div>`;
  }
  if (result.statement) {
    return html`<div class="result-empty">Statement executed (${result.elapsed_ms} ms).</div>`;
  }

  return html`
    <div class="result-wrap" data-testid="results">
      <div class="result-bar">
        <span class="result-stats">
          <b>${result.row_count}</b> ${result.row_count === 1 ? "row" : "rows"}
          ${result.truncated && html`<span class="trunc">(capped at ${result.limit})</span>`}
          · ${result.elapsed_ms} ms
        </span>
        <input
          class="result-filter"
          type="text"
          placeholder="filter rows…"
          value=${filter}
          onInput=${(e) => setFilter(e.target.value)}
        />
      </div>
      <div class="grid-scroll">
        <table class="grid">
          <thead>
            <tr>
              <th class="grid-rownum"></th>
              ${columns.map(
                (c, i) => html`<th
                  key=${c.name}
                  class=${"grid-th " + (isNumericKind(c.type) ? "num" : "")}
                  onClick=${() => toggleSort(i)}
                  title=${c.type}
                >
                  <span class="type-icon">${typeIcon(c.type)}</span>
                  <span class="grid-colname">${c.name}</span>
                  ${sort.col === i && html`<span class="sort-arrow">${sort.dir > 0 ? "▲" : "▼"}</span>`}
                </th>`
              )}
            </tr>
          </thead>
          <tbody>
            ${view.map(
              (row, ri) => html`<tr key=${ri}>
                <td class="grid-rownum">${ri + 1}</td>
                ${row.map(
                  (v, ci) => html`<td
                    key=${ci}
                    class=${isNumericKind(columns[ci] && columns[ci].type) ? "num" : ""}
                  >
                    ${v === null ? html`<span class="null">NULL</span>` : fmtCell(v)}
                  </td>`
                )}
              </tr>`
            )}
            ${view.length === 0 &&
            html`<tr>
              <td class="grid-rownum"></td>
              <td colspan=${columns.length} class="grid-norows">no matching rows</td>
            </tr>`}
          </tbody>
        </table>
      </div>
    </div>
  `;
}
