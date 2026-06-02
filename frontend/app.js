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

function App() {
  const [workspace, setWorkspace] = useState(null);
  const [catalog, setCatalog] = useState([]);
  const [loading, setLoading] = useState(true);
  const [registering, setRegistering] = useState(false);
  const [error, setError] = useState(null);
  const [pick, setPick] = useState(null); // a catalog source picked into the notebook
  const [kernelEnabled, setKernelEnabled] = useState(false);
  const [agentStatus, setAgentStatus] = useState({ enabled: false });
  const [profile, setProfile] = useState(null); // { source, data?, loading, error }
  const [mlSource, setMlSource] = useState(null); // catalog source for the ML panel

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
    refresh();
    // Auto-reload this tab if the server ships a newer frontend build.
    watchForUpdate();
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
        />
        <main class="workbench">
          <${Notebook} catalog=${catalog} pick=${pick} kernelEnabled=${kernelEnabled} agentStatus=${agentStatus} />
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
    </div>
  `;
}

render(html`<${App} />`, document.getElementById("app"));
