import { writeFileSync } from "node:fs";
import { type CliOptions, isAlive, readSession, resolveWorkspace } from "../workspace.ts";
import { vmName, vmRunning } from "../vm/smolvm.ts";

export interface ReplayOptions extends CliOptions {
  /** Notebook id to re-run (required). */
  notebook?: string;
  /** When set, write the rendered HTML report here instead of printing a summary. */
  out?: string;
  /** Pin managed-table reads to the notebook's recorded DuckLake snapshot. */
  reproduce?: boolean;
}

/** Re-run a saved notebook headless against the *already running* session and
 *  refresh its outputs — optionally writing a self-contained HTML report. Cells
 *  (including Python) execute inside the live microVM, so no untrusted code runs
 *  on the host; this command never boots its own VM. */
export async function replayCommand(opts: ReplayOptions): Promise<void> {
  const workspace = resolveWorkspace(opts.path);
  const session = readSession(workspace);
  const name = vmName(workspace);
  const live = (session && isAlive(session.pid)) || vmRunning(name);
  if (!session || !live) {
    console.error(`no running smolduck session for ${workspace}.`);
    console.error("start one first:  smolduck run");
    process.exit(1);
  }

  if (!opts.notebook) {
    console.error("usage: smolduck replay <notebook-id> [path] [--out report.html]");
    process.exit(1);
  }

  const base = `http://127.0.0.1:${session.port}`;
  const wantHtml = !!opts.out;
  const params = new URLSearchParams();
  if (wantHtml) params.set("export", "true");
  if (opts.reproduce) params.set("reproduce", "true");
  const qs = params.toString();
  const url =
    `${base}/api/notebooks/${encodeURIComponent(opts.notebook)}/replay` + (qs ? `?${qs}` : "");

  let resp: Response;
  try {
    resp = await fetch(url, { method: "POST" });
  } catch (err) {
    console.error(`failed to reach the session at ${base}: ${err instanceof Error ? err.message : err}`);
    process.exit(1);
  }
  if (!resp.ok) {
    let detail = await resp.text();
    try {
      detail = (JSON.parse(detail) as { detail?: string }).detail ?? detail;
    } catch {}
    console.error(`replay failed (${resp.status}): ${detail}`);
    process.exit(1);
  }

  if (wantHtml) {
    const htmlOut = opts.out as string;
    writeFileSync(htmlOut, await resp.text());
    console.log(`replayed notebook ${opts.notebook} → ${htmlOut}`);
  } else {
    const nb = (await resp.json()) as { cells?: unknown[] };
    console.log(`replayed notebook ${opts.notebook} — ${nb.cells?.length ?? 0} cell(s) refreshed.`);
  }
}
