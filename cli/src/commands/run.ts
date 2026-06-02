import { existsSync } from "node:fs";
import { randomBytes } from "node:crypto";
import {
  type CliOptions,
  clearSession,
  ensureSmolduckDir,
  isAlive,
  packBasePath,
  packPath,
  readSession,
  repoRoot,
  resolveWorkspace,
  stalePackSources,
  writeSession,
} from "../workspace.ts";
import { bootVm, buildImage, stopVm, vmName, vmRunning } from "../vm/smolvm.ts";

const HEALTH_TIMEOUT_MS = 60_000; // VM boot + extension load is slower than native

export async function runCommand(opts: CliOptions): Promise<void> {
  const workspace = resolveWorkspace(opts.path);
  ensureSmolduckDir(workspace);

  // Guard against a second concurrent run for this workspace. The session pid is
  // the exec supervisor and can be stale/mismatched after an abnormal exit, so we
  // also consult the smolvm registry: a live VM means a session is genuinely up.
  const existing = readSession(workspace);
  const name = vmName(workspace);
  if ((existing && isAlive(existing.pid)) || vmRunning(name)) {
    const where = existing ? ` at ${existing.url}` : "";
    console.error(`smolduck is already running for this workspace${where}`);
    console.error("run `smolduck stop` first.");
    process.exit(1);
  }

  const pack = packPath();
  if (!existsSync(pack)) {
    console.error(`microVM image not found: ${pack}`);
    console.error("build it first with `smolduck build`.");
    process.exit(1);
  }

  // The pack bakes the frontend + backend at build time; if either changed since,
  // the VM would silently serve stale code. Warn and rebuild before booting so the
  // workbench always reflects the current source.
  const stale = stalePackSources();
  if (stale.length > 0) {
    const shown = stale.slice(0, 8);
    console.log(`  ⚠ microVM image is stale — ${stale.length} baked source file(s) changed since the last build:`);
    for (const f of shown) console.log(`      ${f}`);
    if (stale.length > shown.length) console.log(`      …and ${stale.length - shown.length} more`);
    console.log("  rebuilding the image first (smolduck build)…\n");
    try {
      await buildImage(repoRoot(), packBasePath());
    } catch (err) {
      console.error(`rebuild failed: ${err instanceof Error ? err.message : err}`);
      console.error("fix the build or run an older image with care.");
      process.exit(1);
    }
    console.log("");
  }

  const port = opts.port;
  const token = randomBytes(16).toString("hex");
  const url = `http://127.0.0.1:${port}/?t=${token}`;

  console.log(`smolduck · workspace ${workspace}`);
  console.log(`  booting microVM (${name})…`);
  if (opts.readonly) {
    console.log("  --readonly: workspace mounted read-only; session artifacts are discarded on stop");
  }

  let handle;
  try {
    handle = await bootVm({
      workspace,
      port,
      pack,
      mem: opts.mem,
      cpus: opts.cpus,
      readonly: opts.readonly,
      env: { SMOLDUCK_TOKEN: token, ...agentEnv() },
    });
  } catch (err) {
    console.error(`failed to boot microVM: ${err instanceof Error ? err.message : err}`);
    process.exit(1);
  }

  const child = handle.child;

  writeSession(workspace, {
    pid: child.pid,
    port,
    url,
    workspace,
    mode: "vm",
    vmName: name,
    readonly: !!opts.readonly,
    startedAt: new Date().toISOString(),
  });

  let tornDown = false;
  const cleanup = () => {
    if (tornDown) return;
    tornDown = true;
    try {
      child.kill();
    } catch {}
    stopVm(name);
    clearSession(workspace);
  };
  process.on("SIGINT", () => {
    console.log("\nstopping… (tearing down the microVM)");
    cleanup();
    process.exit(0);
  });
  process.on("SIGTERM", () => {
    cleanup();
    process.exit(0);
  });

  const ready = await pollHealth(port, HEALTH_TIMEOUT_MS);
  if (!ready) {
    console.error(`backend did not become healthy on port ${port} within ${HEALTH_TIMEOUT_MS / 1000}s`);
    cleanup();
    process.exit(1);
  }

  console.log(`\n  ✦ workbench ready → ${url}\n`);
  if (opts.open) {
    openBrowser(url);
  } else {
    console.log("  (--no-open: not launching a browser)");
  }

  // The exec supervisor blocks for the session's lifetime.
  await child.exited;
  cleanup();
}

// Forward the optional AI-analyst config into the VM. These are BYO and never
// persisted; the backend reads them at request time and disables the analyst if
// none are present. The matching network egress is opened in bootVm
// (analystEgressFlags): Anthropic → api.anthropic.com; Ollama → host loopback,
// where SMOLDUCK_OLLAMA_HOST=http://localhost:11434 reaches the host daemon.
const AGENT_ENV_KEYS = [
  "ANTHROPIC_API_KEY",
  "SMOLDUCK_AGENT_MODEL",
  "SMOLDUCK_AGENT_FAKE",
  "SMOLDUCK_LLM_PROVIDER",
  "SMOLDUCK_OLLAMA_HOST",
  "SMOLDUCK_OLLAMA_MODEL",
  "OLLAMA_HOST",
];

function agentEnv(): Record<string, string> {
  const env: Record<string, string> = {};
  for (const k of AGENT_ENV_KEYS) {
    const v = process.env[k];
    if (v) env[k] = v;
  }
  return env;
}

async function pollHealth(port: number, timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const resp = await fetch(`http://127.0.0.1:${port}/api/health`);
      if (resp.ok) return true;
    } catch {}
    await Bun.sleep(300);
  }
  return false;
}

function openBrowser(url: string): void {
  const cmd =
    process.platform === "darwin"
      ? ["open", url]
      : process.platform === "win32"
      ? ["cmd", "/c", "start", "", url]
      : ["xdg-open", url];
  try {
    Bun.spawn(cmd, { stdio: ["ignore", "ignore", "ignore"] });
  } catch {}
}
