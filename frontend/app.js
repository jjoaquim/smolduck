import { html } from "htm/preact";
import { render } from "preact";
import { useEffect, useState, useCallback } from "preact/hooks";
import { api } from "./lib/api.js";
import { watchForUpdate } from "./lib/version.js";
import { typeKind } from "./lib/format.js";
import { Catalog } from "./components/Catalog.js";
import { Notebook } from "./components/Notebook.js";
import { ProfilePanel } from "./components/ProfilePanel.js";
import { MlPanel } from "./components/MlPanel.js";
import { CommandPalette } from "./components/CommandPalette.js";

async function loadCatalog() {
  const { sources } = await api.listSources();
  // Enrich each source with its columns via DESCRIBE.
  return Promise.all(
    sources.map(async (s) => {
      try {
        const desc = await api.query(`DESCRIBE "${s.view_name}"`);
        const columns = desc.rows.map((r) => ({ name: r[0], type: r[1], kind: typeKind(r[1]) }));
        return { ...s, columns };
      } catch (_) {
        return { ...s, columns: [] };
      }
    })
  );
}

// The "visible sandbox" badge: turns smolduck's core promise — untrusted code is
// boxed and the VM's only network is the narrowest the analyst needs — into
// something the user can see at a glance. `offline` is the locked, default state.
function EgressBadge({ egress }) {
  const policy = egress.policy || "offline";
  const hosts = egress.allowed_hosts || [];
  const calls = egress.call_count || 0;
  const sandboxed = policy === "offline";
  let label;
  if (policy === "offline") label = "sandboxed · offline";
  else if (policy === "local-only") label = "sandboxed · host loopback";
  else label = `egress · ${hosts.join(", ")}`;
  const callsLabel = calls > 0 ? ` · ${calls} call${calls === 1 ? "" : "s"}` : "";
  const title = sandboxed
    ? "The microVM has no network egress. Untrusted code runs only in the box."
    : `The microVM may reach: ${hosts.join(", ")} (the analyst's provider only). ` +
      `${calls} outbound call${calls === 1 ? "" : "s"} this session.`;
  return html`<span class="egress-badge ${sandboxed ? "locked" : "open"}" title=${title}>
    <span class="egress-icon">${sandboxed ? "🔒" : "🔓"}</span>${label}${callsLabel}
  </span>`;
}

function App() {
  const [workspace, setWorkspace] = useState(null);
  const [catalog, setCatalog] = useState([]);
  const [loading, setLoading] = useState(true);
  const [registering, setRegistering] = useState(false);
  const [error, setError] = useState(null);
  const [pick, setPick] = useState(null); // a catalog source picked into the notebook
  const [kernelEnabled, setKernelEnabled] = useState(false);
  const [agentStatus, setAgentStatus] = useState({ enabled: false });
  const [egress, setEgress] = useState(null); // {policy, allowed_hosts, call_count, ...}
  const [profile, setProfile] = useState(null); // { source, data?, loading, error }
  const [mlSource, setMlSource] = useState(null); // catalog source for the ML panel
  const [loadingExample, setLoadingExample] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [injectSql, setInjectSql] = useState(null); // { sql, n } → appended as a cell by Notebook

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const cat = await loadCatalog();
      setCatalog(cat);
      return cat;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    api.workspace().then((w) => setWorkspace(w)).catch(() => {});
    api.kernelStatus().then((s) => setKernelEnabled(!!s.enabled)).catch(() => {});
    api.agentStatus().then((s) => setAgentStatus(s || { enabled: false })).catch(() => {});
    // The "visible sandbox" badge: poll the egress posture so the analyst call
    // count stays current as the agent runs.
    const pollEgress = () => api.agentEgress().then(setEgress).catch(() => {});
    pollEgress();
    const egressTimer = setInterval(pollEgress, 15000);
    refresh();
    // Auto-reload this tab if the server ships a newer frontend build.
    watchForUpdate();
    return () => clearInterval(egressTimer);
  }, [refresh]);

  const onRegister = useCallback(async () => {
    setRegistering(true);
    setError(null);
    try {
      await api.registerSource(".");
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setRegistering(false);
    }
  }, [refresh]);

  // Bump identity so the notebook reacts even if the same source is picked twice.
  const onPick = useCallback((source) => {
    setPick({ view_name: source.view_name, n: Date.now() });
  }, []);

  const onProfile = useCallback(async (source) => {
    setMlSource(null);
    setProfile({ source, data: null, loading: true, error: null });
    try {
      const data = await api.profileSource(source.id);
      setProfile({ source, data, loading: false, error: null });
    } catch (e) {
      setProfile({ source, data: null, loading: false, error: e.message });
    }
  }, []);

  const onMl = useCallback((source) => {
    setProfile(null);
    setMlSource(source);
  }, []);

  const onLoadExample = useCallback(async () => {
    setLoadingExample(true);
    setError(null);
    try {
      await api.loadExample("sales");
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setLoadingExample(false);
    }
  }, [refresh]);

  // ⌘K / Ctrl-K toggles the command palette (the one global shortcut).
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      } else if (e.key === "Escape") {
        setPaletteOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const onRunSql = useCallback((sql) => {
    setInjectSql({ sql, n: Date.now() });
  }, []);

  const paletteActions = [
    { label: "Register data files in this workspace", hint: "scan folder", run: () => onRegister() },
    { label: "Load example dataset (demo sales)", hint: "demo", run: () => onLoadExample() },
  ];

  const wsName = workspace ? workspace.workspace.split("/").pop() : "…";

  return html`
    <div class="app">
      <header class="topbar">
        <div class="brand">
          <span class="brand-mark">🦆</span>
          <span class="brand-name">smolduck</span>
        </div>
        <div class="workspace" title=${workspace ? workspace.workspace : ""}>${wsName}</div>
        <div class="topbar-spacer"></div>
        ${egress && html`<${EgressBadge} egress=${egress} />`}
        <span class="status-dot ${error ? "err" : "ok"}" title=${error || "backend ready"}></span>
      </header>
      <div class="body">
        <${Catalog}
          catalog=${catalog}
          loading=${loading}
          registering=${registering}
          onRegister=${onRegister}
          onPick=${onPick}
          onProfile=${onProfile}
          onMl=${onMl}
          onLoadExample=${onLoadExample}
          loadingExample=${loadingExample}
        />
        <main class="workbench">
          <${Notebook}
            catalog=${catalog}
            pick=${pick}
            injectSql=${injectSql}
            kernelEnabled=${kernelEnabled}
            agentStatus=${agentStatus}
          />
        </main>
        ${profile &&
        html`<${ProfilePanel}
          profile=${profile.data}
          loading=${profile.loading}
          error=${profile.error}
          onClose=${() => setProfile(null)}
        />`}
        ${mlSource &&
        html`<${MlPanel} source=${mlSource} kernelEnabled=${kernelEnabled} onClose=${() => setMlSource(null)} />`}
      </div>
      <${CommandPalette}
        open=${paletteOpen}
        onClose=${() => setPaletteOpen(false)}
        actions=${paletteActions}
        onRunSql=${onRunSql}
      />
    </div>
  `;
}

render(html`<${App} />`, document.getElementById("app"));
