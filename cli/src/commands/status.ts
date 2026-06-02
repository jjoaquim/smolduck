import { type CliOptions, clearSession, isAlive, readSession, resolveWorkspace } from "../workspace.ts";
import { vmName, vmRunning } from "../vm/smolvm.ts";

export function statusCommand(opts: CliOptions): void {
  const workspace = resolveWorkspace(opts.path);
  const session = readSession(workspace);

  const name = session?.vmName ?? vmName(workspace);
  const running = (session ? isAlive(session.pid) : false) || vmRunning(name);

  if (!session || !running) {
    if (session) clearSession(workspace);
    console.log("no running smolduck session for this workspace.");
    return;
  }

  console.log("smolduck session:");
  console.log(`  workspace : ${session.workspace}`);
  console.log(`  url       : ${session.url}`);
  console.log(`  port      : ${session.port}`);
  console.log(`  pid       : ${session.pid}`);
  console.log(`  mode      : ${session.mode}`);
  console.log(`  vm        : ${name} (${vmRunning(name) ? "running" : "unknown"})`);
  console.log(`  started   : ${session.startedAt}`);
}
