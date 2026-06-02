import { type CliOptions, clearSession, isAlive, readSession, resolveWorkspace } from "../workspace.ts";
import { stopVm, vmName } from "../vm/smolvm.ts";

export function stopCommand(opts: CliOptions): void {
  const workspace = resolveWorkspace(opts.path);
  const session = readSession(workspace);

  // Always tear down the workspace's VM by its deterministic name, even if the
  // session file is missing or stale — disposability must not depend on it.
  const name = session?.vmName ?? vmName(workspace);
  stopVm(name);

  if (session && isAlive(session.pid)) {
    // End the run supervisor process too (stopping the VM EOFs its exec, but be sure).
    try {
      process.kill(session.pid, "SIGTERM");
    } catch {}
  }

  clearSession(workspace);

  if (!session) {
    console.log(`no recorded session; ensured VM '${name}' is stopped. workspace intact: ${workspace}`);
    return;
  }
  console.log(`stopped smolduck (VM '${name}'). workspace left intact: ${workspace}`);
}
