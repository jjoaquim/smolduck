import { html } from "htm/preact";
import { useState } from "preact/hooks";
import { typeIcon } from "../lib/format.js";

function SourceItem({ source, onPick, onProfile, onMl }) {
  const [open, setOpen] = useState(false);
  const cols = source.columns || [];
  return html`
    <li class="cat-source">
      <div class="cat-source-head">
        <button class="cat-toggle" onClick=${() => setOpen(!open)} title="show columns">
          ${open ? "▾" : "▸"}
        </button>
        <button
          class="cat-name"
          onClick=${() => onPick(source)}
          title=${`${source.kind} · ${source.path}`}
        >
          ${source.view_name}
        </button>
        <button class="cat-profile" onClick=${() => onProfile(source)} title="profile this dataset">▤</button>
        <button class="cat-profile cat-ml" onClick=${() => onMl(source)} title="run an ML experiment">⚗</button>
        <span class="cat-kind">${source.kind}</span>
      </div>
      ${open &&
      html`<ul class="cat-cols">
        ${cols.map(
          (c) => html`<li class="cat-col" key=${c.name}>
            <span class="type-icon type-${c.kind}" title=${c.type}>${typeIcon(c.type)}</span>
            <span class="cat-col-name">${c.name}</span>
            <span class="cat-col-type">${c.type}</span>
          </li>`
        )}
        ${cols.length === 0 && html`<li class="cat-col cat-empty">no columns</li>`}
      </ul>`}
    </li>
  `;
}

export function Catalog({ catalog, loading, onPick, onProfile, onMl, onRegister, registering, onLoadExample, loadingExample }) {
  return html`
    <aside class="catalog">
      <div class="rail-head">
        <span class="rail-title">Catalog</span>
        <button class="btn-ghost" onClick=${onRegister} disabled=${registering} title="register data files in this workspace">
          ${registering ? "scanning…" : "+ register"}
        </button>
      </div>
      ${loading
        ? html`<div class="rail-empty">loading…</div>`
        : catalog.length === 0
        ? html`<div class="rail-empty">
            No sources yet.<br />
            <button class="btn" onClick=${onRegister} disabled=${registering}>
              Scan this folder
            </button>
            ${onLoadExample &&
            html`<div class="rail-or">or try a demo</div>
              <button class="btn-ghost" onClick=${onLoadExample} disabled=${loadingExample}
                title="generate a small built-in sales dataset to explore">
                ${loadingExample ? "loading…" : "＋ Load example data"}
              </button>`}
          </div>`
        : html`<ul class="cat-list">
            ${catalog.map(
              (s) =>
                html`<${SourceItem} key=${s.id} source=${s} onPick=${onPick} onProfile=${onProfile} onMl=${onMl} />`
            )}
          </ul>`}
    </aside>
  `;
}
