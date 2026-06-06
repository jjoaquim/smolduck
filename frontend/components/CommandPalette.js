import { html } from "htm/preact";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { api } from "../lib/api.js";

// A ⌘K command palette: actions plus recent query history. Follows the
// PinnedCharts overlay pattern (backdrop + click-outside dismiss). Selecting a
// history entry hands its SQL back to the notebook via onRunSql.
export function CommandPalette({ open, onClose, actions, onRunSql }) {
  const [q, setQ] = useState("");
  const [history, setHistory] = useState([]);
  const [sel, setSel] = useState(0);
  const inputRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    setQ("");
    setSel(0);
    api.listHistory(50).then((r) => setHistory(r.history || [])).catch(() => setHistory([]));
    // Focus the input once the overlay mounts.
    const t = setTimeout(() => inputRef.current && inputRef.current.focus(), 0);
    return () => clearTimeout(t);
  }, [open]);

  // One flat, filtered list of {label, hint, run} so arrow-keys traverse
  // actions and history uniformly.
  const items = useMemo(() => {
    const ql = q.trim().toLowerCase();
    const acts = actions.map((a) => ({ kind: "action", label: a.label, hint: a.hint, run: a.run }));
    const hist = history.map((h) => ({
      kind: "history",
      label: h.sql.replace(/\s+/g, " ").trim(),
      hint: h.ok === false ? "error" : h.row_count != null ? `${h.row_count} rows` : "query",
      run: () => onRunSql(h.sql),
    }));
    const all = [...acts, ...hist];
    if (!ql) return all;
    return all.filter((it) => it.label.toLowerCase().includes(ql) || (it.hint || "").toLowerCase().includes(ql));
  }, [q, history, actions, onRunSql]);

  if (!open) return null;

  const choose = (it) => {
    if (!it) return;
    onClose();
    it.run();
  };

  const onKeyDown = (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSel((s) => Math.min(s + 1, items.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSel((s) => Math.max(s - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(items[sel]);
    } else if (e.key === "Escape") {
      onClose();
    }
  };

  return html`<div class="palette-overlay" onClick=${onClose}>
    <div class="palette-panel" onClick=${(e) => e.stopPropagation()}>
      <input
        ref=${inputRef}
        class="palette-input"
        placeholder="Type a command or search recent queries…"
        value=${q}
        onInput=${(e) => {
          setQ(e.target.value);
          setSel(0);
        }}
        onKeyDown=${onKeyDown}
      />
      <ul class="palette-list">
        ${items.length === 0 && html`<li class="palette-empty">no matches</li>`}
        ${items.map(
          (it, i) => html`<li
            key=${i}
            class=${"palette-item " + (i === sel ? "sel" : "")}
            onMouseEnter=${() => setSel(i)}
            onClick=${() => choose(it)}
          >
            <span class=${"palette-badge " + it.kind}>${it.kind === "history" ? "↺" : "▸"}</span>
            <span class="palette-label">${it.label}</span>
            ${it.hint && html`<span class="palette-hint">${it.hint}</span>`}
          </li>`
        )}
      </ul>
    </div>
  </div>`;
}
