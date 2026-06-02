// Self-heal stale tabs.
//
// ES modules are fetched once and run from a tab's memory until the page is
// reloaded; no-cache response headers only affect the *next* load, so a tab
// opened before an image rebuild keeps executing the old frontend code (the
// trap where editing a cell silently does nothing because the pre-fix editor is
// still running). This watches the server's build id and reloads when it changes
// so an open tab picks up freshly-baked code on its own.

async function fetchBuild() {
  try {
    const r = await fetch("/api/health", { cache: "no-store" });
    if (!r.ok) return null;
    const j = await r.json();
    return j.build || null;
  } catch (_) {
    return null; // offline / backend restarting — try again next tick
  }
}

export async function watchForUpdate({ pollMs = 30000 } = {}) {
  const loaded = await fetchBuild();
  if (!loaded) return; // backend predates build ids — nothing to watch

  let reloading = false;
  const reloadIfStale = async (force) => {
    if (reloading) return;
    const current = await fetchBuild();
    if (current && current !== loaded) {
      // While hidden, reloading is free. While visible, only reload on an
      // explicit trigger (returning to the tab) so we never yank the page out
      // from under active typing.
      if (force || document.hidden) {
        reloading = true;
        location.reload();
      }
    }
  };

  // Returning to a tab that went stale across a rebuild is exactly when the bug
  // would otherwise bite — reload right then, before any interaction.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) reloadIfStale(true);
  });
  window.addEventListener("focus", () => reloadIfStale(true));

  // Backup for a tab left focused the whole time across a rebuild.
  setInterval(() => reloadIfStale(false), pollMs);
}
