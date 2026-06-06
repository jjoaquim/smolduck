// smolvm boot / mount / port-forward wrapper.
//
// The build artifact is a smolvm *pack* (`image/smolduck.smolmachine`) produced
// from a builder VM (see buildImage). A per-session VM is a named smolvm machine
// created `--from` that pack, bind-mounting the workspace and forwarding the UI
// port; the backend runs as a long-lived `machine exec` of the image entrypoint.
//
// Why this exact shape (discovered while integrating against the installed
// smolvm on this host):
//   * The packed self-executable's bundled libkrun runtime does not start here
//     (`./smolduck run`/`start` → "agent did not become ready"), but the smolvm
//     *machine* subsystem boots fine — so we drive `smolvm machine …`.
//   * smolvm reserves the guest `/workspace` path for its own persistent storage
//     (an ext4 overlay shadows a bind there), so the host workspace is mounted at
//     `/data` and the backend is pointed at it via SMOLDUCK_WORKSPACE.
//   * `machine start` does NOT run the image entrypoint, and a server launched as
//     an `--init` command (or a detached exec) is reaped when that command's
//     agent connection closes. A *foreground* `machine exec` of the entrypoint
//     stays alive for as long as the exec connection is held — so that exec IS
//     the session's supervisor process (analogous to the interim uvicorn child).
//   * `smolvm machine stop` on the VM name ends that exec (clean teardown), and
//     the VM is `delete`d so each session is disposable; the workspace persists
//     only through the host bind-mount.

import { createHash } from "node:crypto";

const SMOLVM = "smolvm";
const BUILDER_NAME = "smolduck-builder";
const BUILDER_IMAGE = "python:3.12-slim";
const GUEST_PORT = 4290; // the entrypoint binds here; the host port is forwarded to it
const GUEST_DATA = "/data"; // host workspace mount (NOT /workspace — smolvm reserves that)
const ENTRYPOINT = "/app/entrypoint.sh";

export interface VmBootOptions {
  workspace: string;
  port: number;
  pack: string; // absolute path to image/smolduck.smolmachine
  mem?: string;
  cpus?: string;
  readonly?: boolean;
  env?: Record<string, string>;
}

export interface VmHandle {
  name: string;
  port: number;
  /** The long-lived `machine exec` supervisor running the backend. */
  child: ReturnType<typeof Bun.spawn>;
}

/** Deterministic, collision-resistant VM name for a workspace so `stop`/`status`
 *  from a separate process can find the same machine. */
export function vmName(workspace: string): string {
  const h = createHash("sha256").update(workspace).digest("hex").slice(0, 10);
  return `smolduck-${h}`;
}

interface ShResult {
  ok: boolean;
  out: string;
  err: string;
}

function sh(cmd: string[]): ShResult {
  const p = Bun.spawnSync(cmd, { stdout: "pipe", stderr: "pipe" });
  return {
    ok: p.exitCode === 0,
    out: p.stdout?.toString() ?? "",
    err: p.stderr?.toString() ?? "",
  };
}

/** Run a command with inherited stdio (so the user sees progress) and await it. */
async function shStream(cmd: string[]): Promise<boolean> {
  const p = Bun.spawn(cmd, { stdout: "inherit", stderr: "inherit" });
  return (await p.exited) === 0;
}

function machineExists(name: string): boolean {
  const r = sh([SMOLVM, "machine", "ls", "--json"]);
  if (!r.ok) return false;
  try {
    const list = JSON.parse(r.out) as Array<{ name: string }>;
    return list.some((m) => m.name === name);
  } catch {
    return false;
  }
}

/** Kill any lingering `machine start --name <name>` hypervisor processes.
 *
 *  smolvm's registry tracks at most one machine per name, but each `machine
 *  start` spawns a hypervisor process that holds the host port forward. Reusing
 *  a name (or an abnormal CLI exit that skips cleanup) leaves the previous
 *  hypervisor running and still bound to the port — with no registry entry, so
 *  `machine stop`/`delete` can't reach it. Multiple such orphans bind the same
 *  host port via SO_REUSEPORT; the kernel then spreads requests across them and
 *  stale VMs serve 500s. We find them by their unique command line and kill. */
function killOrphanHypervisors(name: string): void {
  const r = sh(["pgrep", "-f", `machine start --name ${name}`]);
  if (!r.ok) return; // pgrep exits non-zero when nothing matches
  for (const line of r.out.split("\n")) {
    const pid = parseInt(line.trim(), 10);
    if (!Number.isInteger(pid) || pid <= 0) continue;
    try {
      process.kill(pid, "SIGTERM");
    } catch {}
  }
}

/** Delete a machine if it exists (best effort) — used to keep sessions disposable. */
export function deleteMachine(name: string): void {
  if (machineExists(name)) {
    sh([SMOLVM, "machine", "stop", "--name", name]);
    sh([SMOLVM, "machine", "delete", name, "-f"]);
  }
  // Always sweep orphaned hypervisors: they have no registry entry, so the calls
  // above can't reach them, yet they keep the host port bound.
  killOrphanHypervisors(name);
}

/** Boot a per-session VM from the pack and start the backend supervisor.
 *  create + start are synchronous; the returned `child` is the long-lived
 *  `machine exec` running the backend — the caller awaits it and tears down. */
export async function bootVm(opts: VmBootOptions): Promise<VmHandle> {
  const name = vmName(opts.workspace);

  // Disposable: always start from a clean machine of this name.
  deleteMachine(name);

  const mount = opts.readonly
    ? `${opts.workspace}:${GUEST_DATA}:ro`
    : `${opts.workspace}:${GUEST_DATA}`;

  // With a read-only data mount the backend cannot persist artifacts into the
  // workspace, so point it at the VM's ephemeral overlay instead.
  const workspaceEnv = opts.readonly ? "/workspace" : GUEST_DATA;

  const create = [
    SMOLVM, "machine", "create", name,
    "--from", opts.pack,
    "-v", mount,
    "-p", `${opts.port}:${GUEST_PORT}`,
    "-e", `SMOLDUCK_WORKSPACE=${workspaceEnv}`,
    "-e", `SMOLDUCK_READONLY=${opts.readonly ? "1" : "0"}`,
  ];
  for (const [k, v] of Object.entries(opts.env ?? {})) {
    create.push("-e", `${k}=${v}`);
  }
  // The sandbox is offline by default; the AI analyst is the one feature that
  // needs to phone out, so open the narrowest egress its provider requires (and
  // only when one is configured). Without this the backend's request never
  // leaves the VM — getaddrinfo fails with EAI_NONAME ("Name or service not
  // known"), which is what the analyst surfaced.
  for (const flag of analystEgressFlags(opts.env ?? {})) create.push(flag);
  if (opts.cpus) create.push("--cpus", opts.cpus);
  if (opts.mem) create.push("--mem", memToMib(opts.mem));

  const created = sh(create);
  if (!created.ok) {
    throw new Error(`smolvm machine create failed:\n${created.err || created.out}`);
  }

  const started = sh([SMOLVM, "machine", "start", "--name", name]);
  if (!started.ok) {
    deleteMachine(name);
    throw new Error(`smolvm machine start failed:\n${started.err || started.out}`);
  }

  // The backend runs as a foreground exec; this child is the session supervisor.
  const child = Bun.spawn(
    [SMOLVM, "machine", "exec", "--name", name, "--", ENTRYPOINT],
    { stdio: ["inherit", "inherit", "inherit"] }
  );

  return { name, port: opts.port, child };
}

/** The smolvm egress flags the configured analyst provider needs, or `[]` when
 *  no analyst is configured (sandbox stays fully offline).
 *
 *    Ollama    → `--outbound-localhost-only`: reach a daemon on the *host's*
 *                loopback and nothing else. smolvm relays the guest's 127.0.0.1
 *                to the host's, so `SMOLDUCK_OLLAMA_HOST=http://localhost:11434`
 *                works — the backend rewrites the (guest-unresolvable) name
 *                `localhost` to `127.0.0.1` before connecting.
 *    Anthropic → `--allow-host api.anthropic.com`: DNS-filtered egress to the API.
 *
 *  Mirrors providers.py `_selected_name()`/`get_provider()` so the CLI opens
 *  egress for exactly the provider the backend will use. Fake mode makes no
 *  network calls, so it gets none. Any egress also reaches sandboxed code in the
 *  VM — that is the cost of an online analyst, kept as narrow as the provider allows. */
export function analystEgressFlags(env: Record<string, string>): string[] {
  if (env.SMOLDUCK_AGENT_FAKE === "1") return [];
  const explicit = (env.SMOLDUCK_LLM_PROVIDER ?? "").trim().toLowerCase();
  const name = explicit || (env.ANTHROPIC_API_KEY ? "anthropic" : null);
  if (name === "ollama") return ["--outbound-localhost-only"];
  if (name === "anthropic" && env.ANTHROPIC_API_KEY) return ["--allow-host", "api.anthropic.com"];
  return [];
}

export interface EgressPolicy {
  /** Machine-readable policy: `offline` | `local-only` | `allow-host`. */
  policy: "offline" | "local-only" | "allow-host";
  /** The hosts the VM may reach (empty when offline; `127.0.0.1` for local-only). */
  hosts: string[];
  /** A one-line human summary for the boot receipt / teardown proof. */
  label: string;
}

/** Human-readable read of the egress flags the VM was (or would be) created with —
 *  the data behind the "visible sandbox" boot receipt and teardown proof. Derived
 *  from `analystEgressFlags` so it can never disagree with what was actually opened. */
export function egressPolicy(env: Record<string, string>): EgressPolicy {
  const flags = analystEgressFlags(env);
  if (flags.includes("--outbound-localhost-only")) {
    return { policy: "local-only", hosts: ["127.0.0.1"], label: "local-only — host loopback (Ollama), no internet" };
  }
  const at = flags.indexOf("--allow-host");
  if (at >= 0 && flags[at + 1]) {
    const host = flags[at + 1];
    return { policy: "allow-host", hosts: [host], label: `${host} only — no other network egress` };
  }
  return { policy: "offline", hosts: [], label: "offline — no network egress" };
}

/** Stop and delete the session VM (clean, disposable teardown). */
export function stopVm(name: string): void {
  deleteMachine(name);
}

/** Whether the named session VM is currently running. */
export function vmRunning(name: string): boolean {
  const r = sh([SMOLVM, "machine", "ls", "--json"]);
  if (!r.ok) return false;
  try {
    const list = JSON.parse(r.out) as Array<{ name: string; state: string; pid: number | null }>;
    // "unreachable" with a live pid = VM up but agent busy holding our foreground
    // exec supervisor — that is the normal running state for a session VM.
    return list.some(
      (m) => m.name === name && (m.state === "running" || (m.state === "unreachable" && m.pid != null))
    );
  } catch {
    return false;
  }
}

/** Build the image and pack: provision a builder VM, then pack a snapshot.
 *  Needs network (pip + vendored ESM/fonts) at build time only. */
export async function buildImage(repoRoot: string, packOut: string): Promise<void> {
  // Clean any prior builder so the bake is reproducible.
  deleteMachine(BUILDER_NAME);

  console.log(`smolduck build · provisioning builder VM (${BUILDER_IMAGE})`);
  if (!(await shStream([
    SMOLVM, "machine", "create", BUILDER_NAME,
    "--net", "--image", BUILDER_IMAGE,
    "-v", `${repoRoot}:/src:ro`,
  ]))) {
    throw new Error("failed to create builder VM");
  }
  if (!(await shStream([SMOLVM, "machine", "start", "--name", BUILDER_NAME]))) {
    deleteMachine(BUILDER_NAME);
    throw new Error("failed to start builder VM");
  }

  console.log("smolduck build · baking backend + offline frontend + DuckDB extensions");
  const provisioned = await shStream([
    SMOLVM, "machine", "exec", "--name", BUILDER_NAME, "--", "sh", "/src/image/provision.sh",
  ]);
  if (!provisioned) {
    deleteMachine(BUILDER_NAME);
    throw new Error("provisioning failed inside builder VM");
  }

  // pack create reads from a STOPPED VM snapshot.
  sh([SMOLVM, "machine", "stop", "--name", BUILDER_NAME]);

  console.log(`smolduck build · packing → ${packOut}`);
  const packed = await shStream([
    SMOLVM, "pack", "create",
    "--from-vm", BUILDER_NAME,
    "--entrypoint", ENTRYPOINT,
    "-o", packOut,
  ]);
  deleteMachine(BUILDER_NAME);
  if (!packed) {
    throw new Error("smolvm pack create failed");
  }
}

/** Accept `2g`/`2G`/`2048m`/`2048` etc. and normalise to MiB for smolvm `--mem`. */
function memToMib(mem: string): string {
  const m = mem.trim().match(/^(\d+)\s*([gGmM]?)[bB]?$/);
  if (!m) return mem; // pass through; smolvm will validate
  const n = parseInt(m[1], 10);
  const unit = m[2].toLowerCase();
  if (unit === "g") return String(n * 1024);
  return String(n); // already MiB (or unit-less, treated as MiB)
}
