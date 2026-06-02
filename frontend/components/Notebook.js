import { html } from "htm/preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { api, openKernelStream, downloadUrl } from "../lib/api.js";
import { Cell } from "./cells/Cell.js";
import { PinnedCharts } from "./PinnedCharts.js";
import { AgentBar } from "./AgentBar.js";
import { codeFor } from "../lib/chart.js";

const ADD_KINDS = [
  ["sql", "+ SQL"],
  ["python", "+ Python"],
  ["markdown", "+ Markdown"],
  ["chart", "+ Chart"],
];

const DEFAULT_SQL = "-- Write SQL, run with ⌘/Ctrl+Enter\n";

function clientCellId() {
  return "c_" + Math.random().toString(36).slice(2, 10);
}

function summarize(nb) {
  return {
    id: nb.id,
    title: nb.title,
    created_at: nb.created_at,
    updated_at: nb.updated_at,
    cell_count: nb.cells.length,
  };
}

export function Notebook({ catalog, pick, kernelEnabled, agentStatus }) {
  const agentEnabled = !!(agentStatus && agentStatus.enabled);
  const [nb, setNb] = useState(null);
  const [notebooks, setNotebooks] = useState([]);
  const [running, setRunning] = useState({}); // cellId -> bool
  const [errors, setErrors] = useState({}); // cellId -> string (sql)
  const [pyout, setPyout] = useState({}); // cellId -> live python output while running
  const [charts, setCharts] = useState([]); // pinned charts
  const [showPinned, setShowPinned] = useState(false);

  const nbRef = useRef(null);
  const saveTimer = useRef(null);
  const lastPick = useRef(null);

  // ---- persistence -----------------------------------------------------
  function save(next) {
    return api
      .updateNotebook(next.id, {
        title: next.title,
        cells: next.cells.map((c) => ({
          id: c.id,
          kind: c.kind,
          source: c.source,
          last_result: c.last_result ?? null,
          config: c.config ?? null,
        })),
      })
      .then((saved) => setNotebooks((list) => mergeSummary(list, summarize(saved))))
      .catch(() => {});
  }

  function commit(next, { immediate = false } = {}) {
    nbRef.current = next;
    setNb(next);
    clearTimeout(saveTimer.current);
    if (immediate) save(next);
    else saveTimer.current = setTimeout(() => save(nbRef.current), 600);
  }

  function mergeSummary(list, s) {
    const without = list.filter((n) => n.id !== s.id);
    return [s, ...without].sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));
  }

  // ---- initial load ----------------------------------------------------
  useEffect(() => {
    (async () => {
      const list = (await api.listNotebooks()).notebooks;
      if (list.length === 0) {
        const created = await api.createNotebook("Untitled", [{ kind: "sql", source: DEFAULT_SQL }]);
        nbRef.current = created;
        setNb(created);
        setNotebooks([summarize(created)]);
      } else {
        const full = await api.getNotebook(list[0].id);
        nbRef.current = full;
        setNb(full);
        setNotebooks(list);
      }
      refreshPinned();
    })().catch(() => {});
  }, []);

  // ---- cell ops --------------------------------------------------------
  function patchCell(cellId, patch) {
    const cur = nbRef.current;
    return { ...cur, cells: cur.cells.map((c) => (c.id === cellId ? { ...c, ...patch } : c)) };
  }

  function changeSource(cellId, source) {
    commit(patchCell(cellId, { source }));
  }

  function setKind(cellId, kind) {
    commit(patchCell(cellId, { kind, last_result: null }), { immediate: true });
    setErrors((e) => ({ ...e, [cellId]: null }));
  }

  function addCell(kind, source = "") {
    const cell = { id: clientCellId(), kind, source, last_result: null };
    commit({ ...nbRef.current, cells: [...nbRef.current.cells, cell] }, { immediate: true });
  }

  function deleteCell(cellId) {
    commit({ ...nbRef.current, cells: nbRef.current.cells.filter((c) => c.id !== cellId) }, { immediate: true });
  }

  function moveCell(cellId, dir) {
    const cells = nbRef.current.cells.slice();
    const i = cells.findIndex((c) => c.id === cellId);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= cells.length) return;
    [cells[i], cells[j]] = [cells[j], cells[i]];
    commit({ ...nbRef.current, cells }, { immediate: true });
  }

  function runCell(cellId) {
    const cell = nbRef.current.cells.find((c) => c.id === cellId);
    if (!cell) return;
    if (cell.kind === "sql" || cell.kind === "chart") return runSql(cellId, cell);
    if (cell.kind === "python" && kernelEnabled) return runPython(cellId, cell);
  }

  function updateConfig(cellId, config) {
    commit(patchCell(cellId, { config }));
  }

  function insertCellAfter(index, kind, source) {
    const cell = { id: clientCellId(), kind, source, last_result: null, config: null };
    const cells = nbRef.current.cells.slice();
    cells.splice(index + 1, 0, cell);
    commit({ ...nbRef.current, cells }, { immediate: true });
  }

  function copyAsCode(cellId) {
    const cells = nbRef.current.cells;
    const i = cells.findIndex((c) => c.id === cellId);
    if (i < 0) return;
    const cell = cells[i];
    insertCellAfter(i, "python", codeFor(cell.source, cell.config || {}));
  }

  async function refreshPinned() {
    try {
      setCharts((await api.listCharts()).charts);
    } catch (_) {}
  }

  async function pinChart(cellId, figure) {
    const cell = nbRef.current.cells.find((c) => c.id === cellId);
    if (!cell || !figure) return;
    const title = (cell.config && cell.config.title) || `${nbRef.current.title} · chart`;
    try {
      await api.createChart({ title, query: cell.source, config: cell.config || {}, spec: figure });
      await refreshPinned();
      setShowPinned(true);
    } catch (_) {}
  }

  async function deletePinned(id) {
    try {
      await api.deleteChart(id);
      await refreshPinned();
    } catch (_) {}
  }

  async function runSql(cellId, cell) {
    setRunning((r) => ({ ...r, [cellId]: true }));
    setErrors((e) => ({ ...e, [cellId]: null }));
    try {
      const result = await api.query(cell.source);
      commit(patchCell(cellId, { last_result: result }), { immediate: true });
    } catch (err) {
      setErrors((e) => ({ ...e, [cellId]: err.message }));
    } finally {
      setRunning((r) => ({ ...r, [cellId]: false }));
    }
  }

  function runPython(cellId, cell) {
    setRunning((r) => ({ ...r, [cellId]: true }));
    const acc = {
      stdout: "", stderr: "", figures: [], dataframe: null,
      result: null, error: null, timed_out: false, elapsed_ms: null,
    };
    const startedAt = Date.now();
    setPyout((p) => ({ ...p, [cellId]: { ...acc } }));
    let done = false;
    const finish = (ws) => {
      if (done) return;
      done = true;
      try {
        ws.close();
      } catch (_) {}
      acc.elapsed_ms = Date.now() - startedAt;
      commit(patchCell(cellId, { last_result: { ...acc } }), { immediate: true });
      setRunning((r) => ({ ...r, [cellId]: false }));
      setPyout((p) => {
        const n = { ...p };
        delete n[cellId];
        return n;
      });
    };
    const ws = openKernelStream(cell.source, {
      onEvent: (ev) => {
        switch (ev.t) {
          case "stdout": acc.stdout += ev.text; break;
          case "stderr": acc.stderr += ev.text; break;
          case "figure": acc.figures = [...acc.figures, ev.figure]; break;
          case "dataframe":
            acc.dataframe = { columns: ev.columns, rows: ev.rows, shape: ev.shape, truncated: ev.truncated };
            break;
          case "result": acc.result = ev.repr; break;
          case "error": acc.error = ev.error; break;
          case "timeout":
            acc.timed_out = true;
            acc.error = `cell exceeded ${ev.seconds}s timeout — kernel restarted`;
            break;
          case "done":
          case "_closed":
            finish(ws);
            return;
        }
        setPyout((p) => ({ ...p, [cellId]: { ...acc } }));
      },
    });
  }

  // ---- notebook ops ----------------------------------------------------
  function setTitle(title) {
    commit({ ...nbRef.current, title });
  }

  async function switchNotebook(id) {
    if (!id || (nbRef.current && id === nbRef.current.id)) return;
    clearTimeout(saveTimer.current);
    if (nbRef.current) await save(nbRef.current);
    const full = await api.getNotebook(id);
    nbRef.current = full;
    setNb(full);
    setRunning({});
    setErrors({});
  }

  async function newNotebook() {
    clearTimeout(saveTimer.current);
    if (nbRef.current) await save(nbRef.current);
    const created = await api.createNotebook("Untitled", [{ kind: "sql", source: DEFAULT_SQL }]);
    nbRef.current = created;
    setNb(created);
    setNotebooks((list) => mergeSummary(list, summarize(created)));
    setRunning({});
    setErrors({});
  }

  // Catalog "pick" → append a prefilled SQL cell.
  useEffect(() => {
    if (!pick || pick === lastPick.current || !nbRef.current) return;
    lastPick.current = pick;
    addCell("sql", `SELECT *\nFROM ${pick.view_name}\nLIMIT 100;`);
    // eslint-disable-next-line
  }, [pick]);

  if (!nb) {
    return html`<div class="nb-loading">loading notebook…</div>`;
  }

  return html`
    <div class="notebook" data-testid="notebook">
      <div class="nb-bar">
        <input
          class="nb-title"
          value=${nb.title}
          onInput=${(e) => setTitle(e.target.value)}
          title="notebook title"
        />
        <span class="nb-spacer"></span>
        ${notebooks.length > 1 &&
        html`<select
          class="nb-switch"
          value=${nb.id}
          onChange=${(e) => switchNotebook(e.target.value)}
          title="switch notebook"
        >
          ${notebooks.map((n) => html`<option key=${n.id} value=${n.id}>${n.title}</option>`)}
        </select>`}
        <button
          class="btn-ghost"
          data-testid="export-html"
          onClick=${() => downloadUrl(`/api/export/notebook/${nb.id}`, `${nb.title || "notebook"}.html`)}
          title="export this notebook as a standalone HTML report"
        >
          ⤓ HTML
        </button>
        <button class="btn-ghost" onClick=${() => setShowPinned(true)} title="pinned charts">
          📌 ${charts.length}
        </button>
        <button class="btn-ghost" onClick=${newNotebook} title="new notebook">+ notebook</button>
      </div>
      ${showPinned &&
      html`<${PinnedCharts} charts=${charts} onClose=${() => setShowPinned(false)} onDelete=${deletePinned} />`}

      ${agentEnabled && html`<${AgentBar} status=${agentStatus} onPropose=${(cell) => addCell(cell.kind, cell.source)} />`}

      <div class="nb-cells">
        ${nb.cells.map(
          (cell, i) => html`<${Cell}
            key=${`${cell.id}:${cell.kind}`}
            cell=${cell}
            catalog=${catalog}
            index=${i}
            total=${nb.cells.length}
            running=${!!running[cell.id]}
            error=${errors[cell.id] || null}
            kernelEnabled=${kernelEnabled}
            pyout=${pyout[cell.id] || null}
            onChange=${(src) => changeSource(cell.id, src)}
            onRun=${() => runCell(cell.id)}
            onKindChange=${(k) => setKind(cell.id, k)}
            onMoveUp=${() => moveCell(cell.id, -1)}
            onMoveDown=${() => moveCell(cell.id, +1)}
            onDelete=${() => deleteCell(cell.id)}
            onConfigChange=${(cfg) => updateConfig(cell.id, cfg)}
            onCopyAsCode=${() => copyAsCode(cell.id)}
            onPin=${(figure) => pinChart(cell.id, figure)}
          />`
        )}
        ${nb.cells.length === 0 && html`<div class="nb-empty">Empty notebook — add a cell below.</div>`}
      </div>

      <div class="nb-add">
        ${ADD_KINDS.map(
          ([kind, label]) => html`<button
            key=${kind}
            class="btn-ghost nb-add-btn"
            onClick=${() => addCell(kind, kind === "sql" ? DEFAULT_SQL : "")}
          >
            ${label}
          </button>`
        )}
      </div>
    </div>
  `;
}
