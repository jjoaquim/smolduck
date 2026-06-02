import { html } from "htm/preact";
import { useState } from "preact/hooks";
import { api } from "../lib/api.js";

// Natural-language prompt for the AI analyst. The proposed cell is inserted
// review-first (appended, not run) — the user reviews and runs it themselves.
// Only rendered when the agent is enabled (a key is set), so with no key it's
// entirely absent.

export function AgentBar({ onPropose, status }) {
  const model = status && status.model;
  const provider = status && status.provider;
  const via = status && status.fake ? "offline demo" : model ? `${provider} · ${model}` : "AI analyst";
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [err, setErr] = useState(null);

  async function ask() {
    const question = q.trim();
    if (!question || busy) return;
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const res = await api.agentAsk(question);
      if (res.proposed_cell) {
        onPropose(res.proposed_cell);
        setMsg(res.message || "Proposed a cell below — review and run it.");
      } else {
        setMsg(res.message || "No proposal.");
      }
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return html`
    <div class="agent-bar" data-testid="agent-bar">
      <div class="agent-row">
        <button class="agent-toggle" onClick=${() => setOpen(!open)} title=${`ask the AI analyst (${via})`}>
          ✨ Ask the analyst
        </button>
        ${open && html`<span class="agent-via" data-testid="agent-via">${via}</span>`}
        ${open &&
        html`<input
            class="agent-input"
            data-testid="agent-input"
            placeholder="e.g. which region has the most customers?"
            value=${q}
            onInput=${(e) => setQ(e.target.value)}
            onKeyDown=${(e) => e.key === "Enter" && ask()}
          />
          <button class="btn" data-testid="agent-ask" onClick=${ask} disabled=${busy}>
            ${busy ? "thinking…" : "Ask ▸"}
          </button>`}
      </div>
      ${msg && html`<div class="agent-msg" data-testid="agent-msg">${msg}</div>`}
      ${err && html`<div class="agent-err">⚠ ${err}</div>`}
    </div>
  `;
}
