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

  // Teardown proof: make disposability visible — what was destroyed, what the
  // sandbox could reach, and what (only) persists.
  console.log(`stopped smolduck (VM '${name}').`);
  console.log("  ✓ microVM destroyed — kernel and any agent/notebook code went with it");
  if (session.egressHosts && session.egressHosts.length > 0) {
    console.log(`  ✓ network egress was limited to: ${session.egressHosts.join(", ")}`);
  } else {
    console.log("  ✓ the sandbox had no network egress");
  }
  console.log(`  ✓ workspace left intact (the only thing that persists): ${workspace}`);
}
