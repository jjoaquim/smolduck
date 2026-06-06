// fetch + WebSocket clients for the smolduck backend.

const JSON_HEADERS = { "content-type": "application/json" };

async function toError(resp) {
  let detail = resp.statusText;
  try {
    const body = await resp.json();
    detail = body.detail || detail;
  } catch (_) {}
  const err = new Error(detail || `HTTP ${resp.status}`);
  err.status = resp.status;
  return err;
}

async function getJSON(path) {
  const resp = await fetch(path);
  if (!resp.ok) throw await toError(resp);
  return resp.json();
}

async function sendJSON(method, path, body) {
  const resp = await fetch(path, { method, headers: JSON_HEADERS, body: JSON.stringify(body) });
  if (!resp.ok) throw await toError(resp);
  return resp.json();
}

export const api = {
  health: () => getJSON("/api/health"),
  workspace: () => getJSON("/api/workspace"),
  listSources: () => getJSON("/api/sources"),
  registerSource: (path, view_name) => sendJSON("POST", "/api/sources", { path, view_name }),
  deleteSource: (id) =>
    fetch(`/api/sources/${encodeURIComponent(id)}`, { method: "DELETE" }).then((r) => r.json()),
  profileSource: (id) => getJSON(`/api/sources/${encodeURIComponent(id)}/profile`),
  query: (sql, opts = {}) => sendJSON("POST", "/api/query", { sql, ...opts }),
  exportSql: (sql) => sendJSON("POST", "/api/query/export", { sql }), // returns a blob via fetch elsewhere

  listCharts: () => getJSON("/api/charts"),
  createChart: (chart) => sendJSON("POST", "/api/charts", chart),
  deleteChart: (id) =>
    fetch(`/api/charts/${encodeURIComponent(id)}`, { method: "DELETE" }).then((r) => r.json()),

  kernelStatus: () => getJSON("/api/kernel/status"),
  restartKernel: () => sendJSON("POST", "/api/kernel/restart", {}),

  agentStatus: () => getJSON("/api/agent/status"),
  agentEgress: () => getJSON("/api/agent/egress"),
  agentAsk: (question) => sendJSON("POST", "/api/agent/ask", { question }),

  runExperiment: (body) => sendJSON("POST", "/api/ml/experiments", body),
  listExperiments: () => getJSON("/api/ml/experiments"),
  getExperiment: (id) => getJSON(`/api/ml/experiments/${encodeURIComponent(id)}`),

  listNotebooks: () => getJSON("/api/notebooks"),
  getNotebook: (id) => getJSON(`/api/notebooks/${encodeURIComponent(id)}`),
  createNotebook: (title, cells) => sendJSON("POST", "/api/notebooks", { title, cells }),
  updateNotebook: (id, body) => sendJSON("PUT", `/api/notebooks/${encodeURIComponent(id)}`, body),
  deleteNotebook: (id) =>
    fetch(`/api/notebooks/${encodeURIComponent(id)}`, { method: "DELETE" }).then((r) => r.json()),

  listHistory: (limit = 50) => getJSON(`/api/history?limit=${limit}`),
  addHistory: (entry) => sendJSON("POST", "/api/history", entry),
  clearHistory: () => fetch("/api/history", { method: "DELETE" }).then((r) => r.json()),

  listExamples: () => getJSON("/api/examples"),
  loadExample: (name) =>
    sendJSON("POST", `/api/examples/load?name=${encodeURIComponent(name)}`, {}),
};

// Trigger a browser download for a URL (used for the notebook HTML export,
// whose response is served as an attachment).
export function downloadUrl(url, filename) {
  const a = document.createElement("a");
  a.href = url;
  if (filename) a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// POST a request and download the (binary) response as a file.
export async function downloadPost(path, body, filename) {
  const resp = await fetch(path, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body) });
  if (!resp.ok) throw await toError(resp);
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  downloadUrl(url, filename);
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// Stream a large result over /ws/query. Surfaces control frames (schema/end/error)
// and raw Arrow-IPC batch frames; batch decoding is wired up in a later chunk.
export function openQueryStream(sql, { onSchema, onBatch, onEnd, onError, batchSize } = {}) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/query`);
  ws.binaryType = "arraybuffer";
  ws.addEventListener("open", () => ws.send(JSON.stringify({ sql, batch_size: batchSize })));
  ws.addEventListener("message", (ev) => {
    if (typeof ev.data === "string") {
      const msg = JSON.parse(ev.data);
      if (msg.type === "schema") onSchema && onSchema(msg);
      else if (msg.type === "end") onEnd && onEnd(msg);
      else if (msg.type === "error") onError && onError(new Error(msg.error));
    } else {
      onBatch && onBatch(ev.data);
    }
  });
  ws.addEventListener("error", () => onError && onError(new Error("websocket error")));
  return ws;
}

// Stream a Python-kernel execution over /ws/kernel. The server sends one JSON
// event per line (stdout/stderr/figure/dataframe/result/error/timeout/done);
// `onEvent` receives each, plus a synthetic {t:"_closed"} when the socket ends.
export function openKernelStream(code, { onEvent, timeout } = {}) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/kernel`);
  ws.addEventListener("open", () => ws.send(JSON.stringify({ code, timeout })));
  ws.addEventListener("message", (ev) => {
    try {
      onEvent && onEvent(JSON.parse(ev.data));
    } catch (_) {}
  });
  ws.addEventListener("error", () => onEvent && onEvent({ t: "error", error: "websocket error" }));
  ws.addEventListener("close", () => onEvent && onEvent({ t: "_closed" }));
  return ws;
}
