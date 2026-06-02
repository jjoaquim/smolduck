import { join, resolve } from "node:path";
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";

export interface CliOptions {
  path: string;
  port: number;
  open: boolean;
  readonly: boolean;
  mem?: string;
  cpus?: string;
}

export interface Session {
  pid: number;
  port: number;
  url: string;
  workspace: string;
  mode: string;
  readonly: boolean;
  startedAt: string;
  /** smolvm machine name backing this session (mode "vm"). */
  vmName?: string;
}

const SMOLDUCK_DIR = ".smolduck";
const SESSION_FILE = "session.json";

// cli/src/workspace.ts -> repo root is two levels up.
export function repoRoot(): string {
  return resolve(import.meta.dir, "../..");
}
export function backendDir(): string {
  return join(repoRoot(), "backend");
}
export function frontendDir(): string {
  return join(repoRoot(), "frontend");
}
export function imageDir(): string {
  return join(repoRoot(), "image");
}
// `smolvm pack create -o <base>` writes <base> (stub) + <base>.smolmachine (sidecar).
export function packBasePath(): string {
  return join(imageDir(), "smolduck");
}
export function packPath(): string {
  return packBasePath() + ".smolmachine";
}

export function resolveWorkspace(p: string): string {
  return resolve(process.cwd(), p || ".");
}

// Source trees baked into the pack by image/provision.sh. If any of these change
// after a build, the running VM serves stale code — keep this list in sync with
// provision.sh so the staleness check stays honest.
function packSourceInputs(): string[] {
  return [
    join(backendDir(), "app"),
    frontendDir(),
    join(imageDir(), "provision.sh"),
    join(imageDir(), "vendor_assets.py"),
    join(imageDir(), "entrypoint.sh"),
  ];
}

// Noise that never gets baked, so it shouldn't count toward staleness.
function isIgnoredForStaleness(name: string): boolean {
  return name === "__pycache__" || name === ".DS_Store" || name.endsWith(".pyc");
}

function collectFiles(path: string, out: string[]): void {
  let st;
  try {
    st = statSync(path);
  } catch {
    return; // missing input — nothing to compare
  }
  if (st.isFile()) {
    out.push(path);
    return;
  }
  if (st.isDirectory()) {
    for (const entry of readdirSync(path)) {
      if (isIgnoredForStaleness(entry)) continue;
      collectFiles(join(path, entry), out);
    }
  }
}

/** Repo-relative paths of baked source files modified after the pack was built.
 *  Empty when the pack is current or absent — callers gate on `length`. */
export function stalePackSources(): string[] {
  let packMtime: number;
  try {
    packMtime = statSync(packPath()).mtimeMs;
  } catch {
    return []; // no pack yet; the caller handles "build it first"
  }
  const files: string[] = [];
  for (const root of packSourceInputs()) collectFiles(root, files);
  const root = repoRoot() + "/";
  return files
    .filter((f) => {
      try {
        return statSync(f).mtimeMs > packMtime;
      } catch {
        return false;
      }
    })
    .map((f) => (f.startsWith(root) ? f.slice(root.length) : f))
    .sort();
}

export function ensureSmolduckDir(workspace: string): string {
  const dir = join(workspace, SMOLDUCK_DIR);
  mkdirSync(dir, { recursive: true });
  return dir;
}

function sessionPath(workspace: string): string {
  return join(workspace, SMOLDUCK_DIR, SESSION_FILE);
}

export function readSession(workspace: string): Session | null {
  const p = sessionPath(workspace);
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, "utf8")) as Session;
  } catch {
    return null;
  }
}

export function writeSession(workspace: string, session: Session): void {
  ensureSmolduckDir(workspace);
  writeFileSync(sessionPath(workspace), JSON.stringify(session, null, 2));
}

export function clearSession(workspace: string): void {
  const p = sessionPath(workspace);
  try {
    if (existsSync(p)) rmSync(p);
  } catch {}
}

// Liveness check without sending a real signal.
export function isAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}
